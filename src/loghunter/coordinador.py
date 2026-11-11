"""Servidor asyncio: clientes, planificación y reduce distribuido (issues #16 y #18).

Un proceso, un event loop, un hilo. Nunca bloquear el loop: toda llamada
sincrónica a `lector`/Celery/Redis va con `asyncio.to_thread`
(PROJECT_STATEMENT §9.1, invariante 1 de CLAUDE.md). El coordinador pide
tamaño y divide por aritmética; no lee ni parsea el log (invariante 3).

Admisión con `asyncio.Semaphore(max_jobs)`: un pedido sin cupo recibe
`accepted` con `state: queued` y espera sin publicar tareas todavía
(PROJECT_STATEMENT §9.1/§9.2, ADR-0007). El deadline del job arranca al
adquirir el cupo. Cada conexión corre en su propia tarea (`_procesar_job`);
el diccionario de jobs activos solo se muta bajo `self._lock`, sin ningún
`await` dentro de la sección crítica (invariante 5). Dentro de un mismo job,
el estado (`Job`) pertenece exclusivamente a su tarea, así que no necesita
lock propio.

La detección de desconexión corre en una tarea separada que compite con el
procesamiento (`asyncio.wait(..., FIRST_COMPLETED)`): si el cliente cierra
antes de terminar, se cancela el procesamiento, se revocan sus tareas
pendientes y se libera el cupo (CU-22/AC-19), sin afectar otros jobs.

El coordinador encola el agregado completo (antes del recorte `top`) al
indexador, proceso hijo que lanza él mismo (§6, issues #20/#21). Si ese hijo
cae, `_supervisar_indexador` lo detecta y deja de admitir análisis nuevos con
`SERVICE_UNAVAILABLE`; un job que ya estaba en curso falla con el mismo
código al intentar persistir, en vez de anunciar un éxito que no se puede
honrar (CU-23/AC-20). `ipc.Metricas` lleva los contadores compartidos con el
indexador bajo `Value`+`Lock` (issue #21). El cierre ordenado por señales
(`SIGINT`/`SIGTERM`/`SIGUSR1`, issue #22) vive en `_apagar`/`_emitir_snapshot`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import multiprocessing
import os
import signal
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from loghunter import ipc, lector, protocolo
from loghunter.consultas import presentar
from loghunter.workers import calcular_rangos

logger = logging.getLogger(__name__)

_MIB = 2**20
_INTERVALO_PROGRESO = 1.0
_INTERVALO_SUPERVISION = 2.0
_PLAZO_CIERRE = 30.0

LanzarGrupoFn = Callable[[str, int, int, str, str], Any]
ReducirResultadosFn = Callable[[Any], dict[str, Any]]


@dataclass(slots=True)
class Job:
    """Estado de un pedido admitido, propiedad exclusiva de su tarea de conexión."""

    id: str
    file: str
    query: str
    top: int
    chunk_mb: int
    estado: str = "recibido"
    grupo: Any = None
    completados: int = 0
    total: int = 0
    admitido_en: float | None = None
    ts_utc: float | None = None
    deadline: float | None = None


def _cargar_analisis() -> tuple[LanzarGrupoFn, ReducirResultadosFn, type[Exception]]:
    """Importa `loghunter.analisis` recién aquí: evita cargar Celery/Redis al

    importar `coordinador` y permite que `--broker`/`--s3-endpoint` fijen las
    variables de entorno antes de que `tasks.crear_app()` las lea.
    """
    from loghunter import analisis

    return analisis.lanzar_grupo, analisis.reducir_resultados, analisis.TareaFallida


class Coordinador:
    """Admite, despacha y reduce jobs de análisis sin leer el contenido del log."""

    def __init__(
        self,
        *,
        max_jobs: int,
        job_timeout: float,
        chunk_mb_default: int,
        lanzar_grupo: LanzarGrupoFn | None = None,
        reducir_resultados: ReducirResultadosFn | None = None,
        tarea_fallida_exc: type[Exception] | None = None,
        cola_informes: Any | None = None,
        metricas: ipc.Metricas | None = None,
    ) -> None:
        if lanzar_grupo is None or reducir_resultados is None or tarea_fallida_exc is None:
            lanzar_grupo_real, reducir_resultados_real, tarea_fallida_real = _cargar_analisis()
            lanzar_grupo = lanzar_grupo or lanzar_grupo_real
            reducir_resultados = reducir_resultados or reducir_resultados_real
            tarea_fallida_exc = tarea_fallida_exc or tarea_fallida_real

        self.max_jobs = max_jobs
        self.job_timeout = job_timeout
        self.chunk_mb_default = chunk_mb_default
        self._lanzar_grupo = lanzar_grupo
        self._reducir_resultados = reducir_resultados
        self._TareaFallida = tarea_fallida_exc

        self._semaforo = asyncio.Semaphore(max_jobs)
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

        # Persistencia e IPC (issues #20/#21): `cola_informes`/`metricas` son
        # inyectables para pruebas, igual que `lanzar_grupo`; en producción
        # `iniciar_servicios` crea el indexador real si no llegaron ya armados.
        self._cola_informes = cola_informes
        self._metricas = metricas or ipc.Metricas()
        self._proceso_indexador: multiprocessing.Process | None = None
        self._servicio_disponible = True

        # Conexiones activas (en cola o en ejecución) para el apagado ordenado
        # (issue #22): cada entrada vive desde que se crea el job hasta que
        # `atender_cliente` la retira en su `finally`, junto con `_jobs`.
        self._conexiones: dict[str, tuple[Job, asyncio.StreamWriter, asyncio.Task[None]]] = {}
        self._cerrando = False
        self._server: asyncio.base_events.Server | None = None
        self._evento_cierre: asyncio.Event | None = None
        self._tarea_supervision: asyncio.Task[None] | None = None

    async def atender_cliente(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Entrypoint de `asyncio.start_server`: un pedido por conexión."""
        job: Job | None = None
        try:
            try:
                pedido = await protocolo.leer_pedido(reader)
            except protocolo.ErrorProtocolo as error:
                await self._enviar(writer, protocolo.msg_error(None, error.code, error.message))
                return

            job = Job(
                id=_nuevo_job_id(),
                file=pedido["file"],
                query=pedido["query"],
                top=pedido["top"],
                chunk_mb=pedido["chunk_mb"] or self.chunk_mb_default,
            )

            procesar = asyncio.ensure_future(self._procesar_job(job, writer))
            async with self._lock:
                self._conexiones[job.id] = (job, writer, procesar)

            desconexion = asyncio.ensure_future(self._esperar_desconexion(reader))
            terminados, _ = await asyncio.wait(
                {procesar, desconexion}, return_when=asyncio.FIRST_COMPLETED
            )

            if procesar in terminados:
                desconexion.cancel()
                await asyncio.gather(desconexion, return_exceptions=True)
            else:
                procesar.cancel()
                await asyncio.gather(procesar, return_exceptions=True)
        finally:
            if job is not None:
                async with self._lock:
                    self._jobs.pop(job.id, None)
                    self._conexiones.pop(job.id, None)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _esperar_desconexion(self, reader: asyncio.StreamReader) -> None:
        """Completa cuando el cliente cierra o rompe la conexión.

        El protocolo admite un solo pedido por conexión: si llegan más bytes
        es una violación que también tratamos como señal de abandonar la
        espera/el cómputo de ese job.
        """
        try:
            await reader.read(1)
        except (ConnectionError, OSError):
            return

    async def _procesar_job(self, job: Job, writer: asyncio.StreamWriter) -> None:
        if self._cerrando:
            job.estado = "failed"
            await self._enviar(
                writer,
                protocolo.msg_error(job.id, "SERVER_SHUTDOWN", "El coordinador se está apagando."),
            )
            return
        if self._cola_informes is not None and not self._servicio_disponible:
            job.estado = "failed"
            await self._enviar(
                writer,
                protocolo.msg_error(
                    job.id,
                    "SERVICE_UNAVAILABLE",
                    "El indexador no está disponible; no se admiten nuevos análisis.",
                ),
            )
            return

        estado_inicial = "queued" if self._semaforo.locked() else "admitted"
        job.estado = "queued" if estado_inicial == "queued" else "validado"
        await self._enviar(writer, protocolo.msg_accepted(job.id, estado_inicial))

        await self._semaforo.acquire()
        job.estado = "running"
        job.admitido_en = time.monotonic()
        job.ts_utc = time.time()
        job.deadline = job.admitido_en + self.job_timeout
        self._metricas.incrementar_jobs_activos()
        async with self._lock:
            self._jobs[job.id] = job

        try:
            try:
                resultado = await self._ejecutar_analisis(job, writer)
            except protocolo.ErrorProtocolo as error:
                job.estado = "timed_out" if error.code == "JOB_TIMEOUT" else "failed"
                await self._enviar(writer, protocolo.msg_error(job.id, error.code, error.message))
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - frontera de proceso, nunca cae el server
                logger.exception("Fallo inesperado procesando job_id=%s", job.id)
                job.estado = "failed"
                await self._enviar(
                    writer,
                    protocolo.msg_error(
                        job.id, "INTERNAL_ERROR", "Error interno del coordinador."
                    ),
                )
                return
            job.estado = "completed"
            await self._enviar(writer, resultado)
            self._metricas.incrementar_informes_emitidos()
        except asyncio.CancelledError:
            job.estado = "cancelled"
            if job.grupo is not None:
                await asyncio.to_thread(_revocar_grupo, job.grupo)
            raise
        finally:
            self._metricas.decrementar_jobs_activos()
            self._semaforo.release()

    async def _ejecutar_analisis(self, job: Job, writer: asyncio.StreamWriter) -> dict[str, Any]:
        chunk_bytes = job.chunk_mb * _MIB
        tamano = await asyncio.to_thread(_tamano_archivo, job.file)

        rangos = calcular_rangos(tamano, chunk_bytes) if tamano > 0 else []
        job.total = len(rangos)
        await self._enviar(writer, protocolo.msg_started(job.id, job.total, job.chunk_mb))

        if job.total == 0:
            reducido = {"contador": {}, "lineas": 0, "invalidas": 0, "workers": 0}
        else:
            try:
                job.grupo = await asyncio.to_thread(
                    self._lanzar_grupo, job.file, tamano, chunk_bytes, job.query, job.id
                )
            except Exception as exc:
                raise protocolo.ErrorProtocolo(
                    "BROKER_UNAVAILABLE", f"No se pudo publicar el análisis: {exc}"
                ) from exc

            try:
                reducido = await self._esperar_grupo(job, writer)
            except self._TareaFallida as exc:
                await asyncio.to_thread(_revocar_grupo, job.grupo)
                raise protocolo.ErrorProtocolo("TASK_FAILED", str(exc)) from exc
            except TimeoutError as exc:
                await asyncio.to_thread(_revocar_grupo, job.grupo)
                raise protocolo.ErrorProtocolo(
                    "JOB_TIMEOUT", "El análisis superó el tiempo permitido."
                ) from exc
            except Exception as exc:
                await asyncio.to_thread(_revocar_grupo, job.grupo)
                raise protocolo.ErrorProtocolo(
                    "BROKER_UNAVAILABLE", f"Fallo de infraestructura reduciendo parciales: {exc}"
                ) from exc

        # "Reduce exitoso" ya ocurrió llegados a este punto (incluido el caso
        # de archivo vacío); PROJECT_STATEMENT §11 exige contarlo una sola vez
        # por job, independientemente de si la persistencia luego falla.
        self._metricas.sumar_lineas_validas(reducido["lineas"])

        duracion = time.monotonic() - job.admitido_en
        contador_total = Counter(reducido["contador"])
        top = job.top if job.query in ("top-ips", "urls") else None
        resultados = presentar(job.query, contador_total, top)

        persistencia = await self._persistir_informe(
            job, tamano, reducido, contador_total, duracion
        )

        return protocolo.msg_result(
            job.id,
            file=job.file,
            query=job.query,
            top=job.top,
            chunk_mb=job.chunk_mb,
            tamano_bytes=tamano,
            chunks=job.total,
            workers=reducido["workers"],
            lineas=reducido["lineas"],
            invalidas=reducido["invalidas"],
            lineas_totales=reducido["lineas"] + reducido["invalidas"],
            duracion=duracion,
            resultados=[{"clave": clave, "valor": valor} for clave, valor in resultados],
            persistencia=persistencia,
        )

    async def _persistir_informe(
        self,
        job: Job,
        tamano: int,
        reducido: dict[str, Any],
        contador_total: Counter[str],
        duracion: float,
    ) -> str:
        """Encola el agregado completo (antes del `top`) para el indexador.

        Sin cola configurada no hay indexador en este `Coordinador` (modo de
        prueba de la capa de jobs, issue #19): no es un fallo, es que esta
        instancia no tiene persistencia conectada. Con cola configurada, un
        indexador caído no se sigue intentando encolar indefinidamente sin
        consumidor (CU-23): falla el job con `SERVICE_UNAVAILABLE` en vez de
        anunciar un éxito que nunca se va a persistir.
        """
        if self._cola_informes is None:
            return "no_configurada"
        if not self._servicio_disponible:
            raise protocolo.ErrorProtocolo(
                "SERVICE_UNAVAILABLE",
                "El indexador no está disponible; no se pudo persistir el informe.",
            )

        resultados_completos = presentar(job.query, contador_total, top=None)
        informe = {
            "job_id": job.id,
            "ts": job.ts_utc,
            "archivo": job.file,
            "tamano_bytes": tamano,
            "query": job.query,
            "top": job.top,
            "chunk_mb": job.chunk_mb,
            "chunks": job.total,
            "workers": reducido["workers"],
            "lineas": reducido["lineas"],
            "invalidas": reducido["invalidas"],
            "duracion": duracion,
            "resultados": [{"clave": clave, "valor": valor} for clave, valor in resultados_completos],
        }
        try:
            await asyncio.to_thread(self._cola_informes.put, informe)
        except Exception as exc:
            raise protocolo.ErrorProtocolo(
                "SERVICE_UNAVAILABLE", f"No se pudo encolar el informe: {exc}"
            ) from exc
        return "encolada"

    async def _esperar_grupo(self, job: Job, writer: asyncio.StreamWriter) -> dict[str, Any]:
        """Reduce el grupo de `job` emitiendo progreso monótono en paralelo.

        El reduce bloqueante corre en un hilo (`asyncio.to_thread`); una
        corrutina separada sondea el conteo de tramos exitosos cada segundo
        y lo envía como `progress`, sin esperar a que el reduce termine.
        """
        fin = asyncio.Event()

        async def _progreso() -> None:
            anterior = -1
            while not fin.is_set():
                completados = await asyncio.to_thread(_contar_completados, job.grupo)
                if completados != anterior:
                    job.completados = completados
                    await self._enviar(writer, protocolo.msg_progress(job.id, completados, job.total))
                    anterior = completados
                try:
                    await asyncio.wait_for(fin.wait(), timeout=_INTERVALO_PROGRESO)
                except asyncio.TimeoutError:
                    pass

        restante = job.deadline - time.monotonic()
        if restante <= 0:
            raise TimeoutError("El deadline del job venció antes de iniciar el reduce")

        tarea_progreso = asyncio.ensure_future(_progreso())
        try:
            return await asyncio.to_thread(self._reducir_resultados, job.grupo, restante, 0.5)
        finally:
            fin.set()
            await tarea_progreso

    async def _enviar(self, writer: asyncio.StreamWriter, mensaje: dict[str, Any]) -> None:
        try:
            writer.write(protocolo.codificar(mensaje))
            await writer.drain()
        except (ConnectionError, RuntimeError, OSError):
            pass

    # -- Indexador, supervisión y señales (issues #20/#21/#22) --------------

    def iniciar_servicios(self, server: asyncio.base_events.Server, db_path: str) -> None:
        """Arranca el indexador (si no llegó ya armado), la supervisión y las
        señales. Solo tiene sentido llamarla una vez el loop está corriendo:
        `add_signal_handler` lo exige."""
        self._server = server
        self._evento_cierre = asyncio.Event()
        if self._cola_informes is None:
            self._iniciar_indexador(db_path)
        self._tarea_supervision = asyncio.ensure_future(self._supervisar_indexador())

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._agendar_apagado)
        loop.add_signal_handler(signal.SIGTERM, self._agendar_apagado)
        loop.add_signal_handler(signal.SIGUSR1, self._agendar_snapshot)

    async def esperar_cierre(self) -> None:
        assert self._evento_cierre is not None, "iniciar_servicios() no fue llamado"
        await self._evento_cierre.wait()

    def _iniciar_indexador(self, db_path: str) -> None:
        from loghunter import indexador

        self._cola_informes = multiprocessing.Queue()
        self._proceso_indexador = multiprocessing.Process(
            target=indexador.ejecutar,
            args=(self._cola_informes, db_path, self._metricas),
        )
        self._proceso_indexador.start()
        logger.info("Indexador iniciado: pid=%s db=%s", self._proceso_indexador.pid, db_path)

    async def _supervisar_indexador(self) -> None:
        """Detecta la caída del hijo (CU-23): `is_alive()`/`exitcode` solo
        consultan el estado ya conocido del proceso, no bloquean."""
        try:
            while True:
                await asyncio.sleep(_INTERVALO_SUPERVISION)
                if (
                    self._proceso_indexador is not None
                    and self._servicio_disponible
                    and not self._proceso_indexador.is_alive()
                ):
                    self._servicio_disponible = False
                    logger.critical(
                        "El indexador terminó inesperadamente (exitcode=%s); "
                        "dejo de admitir nuevos análisis",
                        self._proceso_indexador.exitcode,
                    )
        except asyncio.CancelledError:
            pass

    def _agendar_snapshot(self) -> None:
        """Handler de SIGUSR1: solo agenda la corrutina, sin I/O en el handler."""
        asyncio.ensure_future(self._emitir_snapshot())

    async def _emitir_snapshot(self) -> None:
        async with self._lock:
            jobs = [
                (
                    job.id,
                    job.estado,
                    job.completados,
                    job.total,
                    time.monotonic() - job.admitido_en if job.admitido_en is not None else 0.0,
                )
                for job in self._jobs.values()
            ]
        logger.info("snapshot (SIGUSR1): %d job(s) activo(s)", len(jobs))
        for job_id, estado, completados, total, transcurrido in jobs:
            logger.info(
                "  job_id=%s estado=%s progreso=%d/%d transcurrido=%.1fs",
                job_id, estado, completados, total, transcurrido,
            )
        logger.info("snapshot (SIGUSR1): contadores=%s", self._metricas.snapshot())

    def _agendar_apagado(self) -> None:
        """Handler de SIGINT/SIGTERM: agenda el apagado, nunca bloquea aquí."""
        asyncio.ensure_future(self._apagar())

    async def _apagar(self) -> None:
        """Cierre ordenado (CU-19): deja de admitir, avisa y cancela jobs,
        drena el indexador y espera al hijo fuera del loop antes de salir."""
        if self._cerrando:
            return
        self._cerrando = True
        logger.info("Apagado solicitado: dejo de admitir conexiones nuevas")
        if self._server is not None:
            self._server.close()

        async with self._lock:
            conexiones = list(self._conexiones.values())
        for job, writer, tarea in conexiones:
            await self._enviar(
                writer,
                protocolo.msg_error(job.id, "SERVER_SHUTDOWN", "El coordinador se está apagando."),
            )
            tarea.cancel()
        if conexiones:
            await asyncio.gather(*(tarea for _, _, tarea in conexiones), return_exceptions=True)

        if self._cola_informes is not None:
            await asyncio.to_thread(self._cola_informes.put, None)  # centinela tras lo ya encolado
        if self._proceso_indexador is not None:
            await asyncio.to_thread(self._proceso_indexador.join, _PLAZO_CIERRE)
            if self._proceso_indexador.is_alive():
                logger.error(
                    "El indexador no terminó en %.0fs; aplico terminación forzada "
                    "(no se garantiza que haya drenado todos los informes)",
                    _PLAZO_CIERRE,
                )
                self._proceso_indexador.terminate()
                await asyncio.to_thread(self._proceso_indexador.join, 5.0)
        if self._cola_informes is not None:
            self._cola_informes.close()
        if self._tarea_supervision is not None:
            self._tarea_supervision.cancel()
            await asyncio.gather(self._tarea_supervision, return_exceptions=True)

        if self._evento_cierre is not None:
            self._evento_cierre.set()


def _nuevo_job_id() -> str:
    return uuid.uuid4().hex


def _tamano_archivo(uri: str) -> int:
    """Abre el lector solo para consultar el tamaño y lo cierra de inmediato."""
    try:
        recurso = lector.abrir(uri)
    except FileNotFoundError as exc:
        raise protocolo.ErrorProtocolo("FILE_NOT_FOUND", str(exc)) from exc
    except PermissionError as exc:
        raise protocolo.ErrorProtocolo("FILE_ACCESS_ERROR", str(exc)) from exc
    except ValueError as exc:
        raise protocolo.ErrorProtocolo("UNSUPPORTED_URI", str(exc)) from exc
    except lector.AlmacenamientoNoDisponible as exc:
        raise protocolo.ErrorProtocolo("STORAGE_UNAVAILABLE", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - p.ej. credenciales S3 ausentes
        raise protocolo.ErrorProtocolo("STORAGE_UNAVAILABLE", str(exc)) from exc

    try:
        return recurso.tamano()
    finally:
        recurso.cerrar()


def _contar_completados(grupo: Any) -> int:
    return sum(1 for resultado in grupo.results if resultado.ready() and resultado.successful())


def _revocar_grupo(grupo: Any) -> None:
    """Revoca solo las tareas pendientes de `grupo`; nunca workers globales."""
    for resultado in grupo.results:
        resultado.revoke()


def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return valor


def _puerto(texto: str) -> int:
    valor = int(texto)
    if not (1 <= valor <= 65535):
        raise argparse.ArgumentTypeError("el puerto debe estar entre 1 y 65535")
    return valor


def _chunk_mb(texto: str) -> int:
    valor = int(texto)
    if not (1 <= valor <= 256):
        raise argparse.ArgumentTypeError("--chunk-mb debe estar entre 1 y 256 MiB")
    return valor


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m loghunter.coordinador",
        description="Coordinador LogHunter: atiende clientes TCP, planifica y reduce.",
    )
    parser.add_argument("-i", "--ip", default="0.0.0.0", help="Dirección de escucha (default: 0.0.0.0)")
    parser.add_argument("-p", "--port", type=_puerto, default=8888, help="Puerto 1-65535 (default: 8888)")
    parser.add_argument(
        "--chunk-mb", type=_chunk_mb, default=32, help="Tamaño de tramo en MiB, 1-256 (default: 32)"
    )
    parser.add_argument(
        "--max-jobs", type=_entero_positivo, default=3, help="Análisis simultáneos admitidos (default: 3)"
    )
    parser.add_argument(
        "--job-timeout",
        type=_entero_positivo,
        default=300,
        help="Segundos desde la admisión hasta el reduce (default: 300)",
    )
    parser.add_argument(
        "--broker", default=None, help="URL de Redis; si se omite, usa CELERY_BROKER_URL o su default"
    )
    parser.add_argument(
        "--s3-endpoint", default=None, help="Endpoint de MinIO; si se omite, usa S3_ENDPOINT o su default"
    )
    parser.add_argument("--db", default="informes.sqlite", help="Ruta de SQLite del indexador (default: informes.sqlite)")
    return parser


async def _servir(coordinador: Coordinador, ip: str, port: int, db_path: str) -> None:
    servidor = await asyncio.start_server(
        coordinador.atender_cliente, host=ip, port=port, limit=protocolo.LIMITE_PEDIDO
    )
    direcciones = ", ".join(str(sock.getsockname()) for sock in servidor.sockets or ())
    logger.info("Coordinador escuchando en %s", direcciones)
    coordinador.iniciar_servicios(servidor, db_path)
    try:
        await coordinador.esperar_cierre()
    finally:
        servidor.close()
        await servidor.wait_closed()


def main(argv: list[str] | None = None) -> None:
    args = construir_parser().parse_args(argv)
    if args.broker:
        os.environ["CELERY_BROKER_URL"] = args.broker
    if args.s3_endpoint:
        os.environ["S3_ENDPOINT"] = args.s3_endpoint

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    coordinador = Coordinador(
        max_jobs=args.max_jobs, job_timeout=args.job_timeout, chunk_mb_default=args.chunk_mb
    )
    try:
        asyncio.run(_servir(coordinador, args.ip, args.port, args.db))
    except KeyboardInterrupt:
        logger.info("Coordinador detenido por el operador")


if __name__ == "__main__":
    main()

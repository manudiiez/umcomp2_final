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

La persistencia en SQLite (indexador, issues #20/#21) todavía no existe:
`persistencia` en el `result` queda en un valor fijo hasta esa etapa.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from loghunter import lector, protocolo
from loghunter.consultas import presentar
from loghunter.workers import calcular_rangos

logger = logging.getLogger(__name__)

_MIB = 2**20
_PERSISTENCIA_PENDIENTE = "no_implementada"  # se resuelve al integrar el indexador (#20/#21)
_INTERVALO_PROGRESO = 1.0

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
        estado_inicial = "queued" if self._semaforo.locked() else "admitted"
        job.estado = "queued" if estado_inicial == "queued" else "validado"
        await self._enviar(writer, protocolo.msg_accepted(job.id, estado_inicial))

        await self._semaforo.acquire()
        job.estado = "running"
        job.admitido_en = time.monotonic()
        job.deadline = job.admitido_en + self.job_timeout
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
        except asyncio.CancelledError:
            job.estado = "cancelled"
            if job.grupo is not None:
                await asyncio.to_thread(_revocar_grupo, job.grupo)
            raise
        finally:
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

        duracion = time.monotonic() - job.admitido_en
        top = job.top if job.query in ("top-ips", "urls") else None
        resultados = presentar(job.query, Counter(reducido["contador"]), top)

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
            persistencia=_PERSISTENCIA_PENDIENTE,
        )

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
    parser.add_argument("--db", default="informes.sqlite", help="Ruta de SQLite (indexador, aún sin integrar)")
    return parser


async def _servir(coordinador: Coordinador, ip: str, port: int) -> None:
    servidor = await asyncio.start_server(
        coordinador.atender_cliente, host=ip, port=port, limit=protocolo.LIMITE_PEDIDO
    )
    direcciones = ", ".join(str(sock.getsockname()) for sock in servidor.sockets or ())
    logger.info("Coordinador escuchando en %s", direcciones)
    async with servidor:
        await servidor.serve_forever()


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
        asyncio.run(_servir(coordinador, args.ip, args.port))
    except KeyboardInterrupt:
        logger.info("Coordinador detenido por el operador")


if __name__ == "__main__":
    main()

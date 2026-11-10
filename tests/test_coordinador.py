"""Pruebas de concurrencia y ciclo de vida de jobs (issue #19).

PROJECT_STATEMENT §9, ADR-0007. `lanzar_grupo`/`reducir_resultados` se
inyectan como fakes controlables por evento: así se puede fijar con
precisión cuándo "termina" cada tramo sin depender de Redis/Celery reales
(esos recorridos de punta a punta están en test_tasks.py/test_analisis.py/
test_integracion_distribucion.py). Lo que se verifica aquí es exclusivo de
la capa de jobs: admisión con semáforo, aislamiento entre `job_id`,
timeout/fallo que no afectan a otros pedidos y limpieza ante desconexión
(CU-08, CU-09, CU-14, CU-22; AC-05, AC-06, AC-11, AC-19).
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from loghunter import coordinador, protocolo


class _TareaFallidaFalsa(Exception):
    pass


class _ResultadoFalso:
    def __init__(self) -> None:
        self.revocado = False

    def ready(self) -> bool:
        return True

    def successful(self) -> bool:
        return True

    def revoke(self) -> None:
        self.revocado = True


class _GrupoFalso:
    def __init__(self, job_id: str, cantidad: int = 1) -> None:
        self.id = job_id
        self.results = [_ResultadoFalso() for _ in range(cantidad)]


class _Control:
    """Decide cuándo y cómo "termina" el reduce simulado de cada job_id."""

    def __init__(self) -> None:
        self._eventos: dict[str, threading.Event] = {}
        self._acciones: dict[str, str] = {}
        self._lock = threading.Lock()
        self.lanzados: list[str] = []
        self.reducidos: list[str] = []
        self.grupos: dict[str, _GrupoFalso] = {}

    def _evento(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._eventos.setdefault(job_id, threading.Event())

    def liberar(self, job_id: str, accion: str = "ok") -> None:
        with self._lock:
            self._acciones[job_id] = accion
        self._evento(job_id).set()

    def lanzar_grupo(
        self, uri: str, tamano: int, chunk_bytes: int, query: str, job_id: str
    ) -> _GrupoFalso:
        self.lanzados.append(job_id)
        grupo = _GrupoFalso(job_id)
        self.grupos[job_id] = grupo
        return grupo

    def reducir_resultados(
        self, grupo: _GrupoFalso, timeout: float | None = None, intervalo: float = 0.2
    ) -> dict[str, Any]:
        self.reducidos.append(grupo.id)
        if not self._evento(grupo.id).wait(timeout=timeout):
            raise TimeoutError("timeout simulado de reduce")
        if self._acciones.get(grupo.id) == "fail":
            raise _TareaFallidaFalsa("tramo fallido simulado")
        return {"contador": {"200": 3, "404": 1}, "lineas": 4, "invalidas": 0, "workers": 1}


def _lanzar_grupo_roto(uri: str, tamano: int, chunk_bytes: int, query: str, job_id: str) -> Any:
    raise ConnectionRefusedError("Redis no disponible (simulado)")


def _coordinador(control: _Control, **kwargs: Any) -> coordinador.Coordinador:
    base: dict[str, Any] = {"max_jobs": 3, "job_timeout": 5.0, "chunk_mb_default": 32}
    base.update(kwargs)
    return coordinador.Coordinador(
        lanzar_grupo=control.lanzar_grupo,
        reducir_resultados=control.reducir_resultados,
        tarea_fallida_exc=_TareaFallidaFalsa,
        **base,
    )


async def _iniciar_servidor(coord: coordinador.Coordinador) -> tuple[asyncio.Server, int]:
    servidor = await asyncio.start_server(
        coord.atender_cliente, host="127.0.0.1", port=0, limit=protocolo.LIMITE_PEDIDO
    )
    puerto = servidor.sockets[0].getsockname()[1]
    return servidor, puerto


async def _conectar(puerto: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", puerto)


async def _enviar(writer: asyncio.StreamWriter, **campos: Any) -> None:
    pedido = {"version": 1, "type": "analyze", "query": "status", **campos}
    writer.write(protocolo.codificar(pedido))
    await writer.drain()


async def _leer(reader: asyncio.StreamReader) -> dict[str, Any]:
    linea = await reader.readline()
    assert linea, "la conexión se cerró sin un mensaje"
    return json.loads(linea)


async def _leer_hasta_terminal(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Lee mensajes descartando `progress` hasta encontrar `result`/`error`.

    Los fakes de este módulo reportan `ready()`/`successful()` por tramo sin
    simular el bloqueo de `reducir_resultados`, así que la corrutina de
    progreso puede emitir mensajes antes del terminal; no forma parte de lo
    que estas pruebas verifican.
    """
    while True:
        mensaje = await _leer(reader)
        if mensaje["type"] in ("result", "error"):
            return mensaje


def _archivo(tmp_path: Path, nombre: str = "a.log", contenido: bytes = b"x" * 64) -> str:
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return f"local://{ruta}"


def test_dos_clientes_concurrentes_no_mezclan_progreso_ni_resultados(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=2)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader_a, writer_a = await _conectar(puerto)
            reader_b, writer_b = await _conectar(puerto)

            await _enviar(writer_a, file=_archivo(tmp_path, "a.log"), query="top-ips")
            await _enviar(writer_b, file=_archivo(tmp_path, "b.log"), query="status")

            acc_a = await _leer(reader_a)
            acc_b = await _leer(reader_b)
            assert acc_a["state"] == "admitted"
            assert acc_b["state"] == "admitted"
            job_a, job_b = acc_a["job_id"], acc_b["job_id"]
            assert job_a != job_b

            started_a = await _leer(reader_a)
            started_b = await _leer(reader_b)
            assert started_a["chunks"] == 1
            assert started_b["chunks"] == 1

            # B termina primero: A sigue bloqueado y no interfiere.
            control.liberar(job_b)
            resultado_b = await _leer_hasta_terminal(reader_b)
            assert resultado_b["type"] == "result"
            assert resultado_b["job_id"] == job_b
            assert resultado_b["query"] == "status"

            control.liberar(job_a)
            resultado_a = await _leer_hasta_terminal(reader_a)
            assert resultado_a["type"] == "result"
            assert resultado_a["job_id"] == job_a
            assert resultado_a["query"] == "top-ips"

            writer_a.close()
            writer_b.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_max_jobs_uno_el_segundo_espera_y_arranca_al_liberarse_el_cupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader_a, writer_a = await _conectar(puerto)
            await _enviar(writer_a, file=_archivo(tmp_path, "a.log"))
            acc_a = await _leer(reader_a)
            assert acc_a["state"] == "admitted"
            job_a = acc_a["job_id"]
            await _leer(reader_a)  # started

            reader_b, writer_b = await _conectar(puerto)
            await _enviar(writer_b, file=_archivo(tmp_path, "b.log"))
            acc_b = await _leer(reader_b)
            assert acc_b["state"] == "queued"
            job_b = acc_b["job_id"]

            # B espera: todavía no se publicó ningún grupo para su job.
            assert job_b not in control.lanzados

            control.liberar(job_a)
            resultado_a = await _leer_hasta_terminal(reader_a)
            assert resultado_a["type"] == "result"

            started_b = await _leer(reader_b)
            assert started_b["type"] == "started"
            control.liberar(job_b)
            resultado_b = await _leer_hasta_terminal(reader_b)
            assert resultado_b["type"] == "result"
            assert resultado_b["job_id"] == job_b

            writer_a.close()
            writer_b.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_timeout_de_un_job_no_cancela_al_otro_y_libera_su_cupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=2, job_timeout=0.2)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader_a, writer_a = await _conectar(puerto)
            reader_b, writer_b = await _conectar(puerto)

            await _enviar(writer_a, file=_archivo(tmp_path, "a.log"))
            await _enviar(writer_b, file=_archivo(tmp_path, "b.log"))

            acc_a = await _leer(reader_a)
            acc_b = await _leer(reader_b)
            job_a, job_b = acc_a["job_id"], acc_b["job_id"]
            await _leer(reader_a)  # started A
            await _leer(reader_b)  # started B

            # A nunca se libera: debe vencer su deadline sin afectar a B.
            control.liberar(job_b)
            resultado_b = await _leer_hasta_terminal(reader_b)
            assert resultado_b["type"] == "result"

            error_a = await _leer_hasta_terminal(reader_a)
            assert error_a["type"] == "error"
            assert error_a["code"] == "JOB_TIMEOUT"
            assert control.grupos[job_a].results[0].revocado is True

            writer_a.close()
            writer_b.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_fallo_de_tarea_produce_task_failed_y_revoca_el_grupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            job_id = acc["job_id"]
            await _leer(reader)  # started

            control.liberar(job_id, accion="fail")
            error = await _leer_hasta_terminal(reader)
            assert error["type"] == "error"
            assert error["code"] == "TASK_FAILED"
            assert control.grupos[job_id].results[0].revocado is True

            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_fallo_al_publicar_el_grupo_produce_broker_unavailable(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = coordinador.Coordinador(
            max_jobs=1,
            job_timeout=5.0,
            chunk_mb_default=32,
            lanzar_grupo=_lanzar_grupo_roto,
            reducir_resultados=control.reducir_resultados,
            tarea_fallida_exc=_TareaFallidaFalsa,
        )
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            await _leer(reader)  # accepted
            await _leer(reader)  # started
            error = await _leer_hasta_terminal(reader)
            assert error["type"] == "error"
            assert error["code"] == "BROKER_UNAVAILABLE"
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_desconexion_mientras_espera_cupo_lo_libera_sin_publicar_tareas(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader_a, writer_a = await _conectar(puerto)
            await _enviar(writer_a, file=_archivo(tmp_path, "a.log"))
            acc_a = await _leer(reader_a)
            job_a = acc_a["job_id"]
            await _leer(reader_a)  # started

            reader_b, writer_b = await _conectar(puerto)
            await _enviar(writer_b, file=_archivo(tmp_path, "b.log"))
            acc_b = await _leer(reader_b)
            assert acc_b["state"] == "queued"
            job_b = acc_b["job_id"]

            writer_b.close()
            await asyncio.sleep(0.1)  # darle tiempo al servidor a detectar el cierre

            assert job_b not in control.lanzados
            assert job_b not in coord._jobs

            control.liberar(job_a)
            resultado_a = await _leer_hasta_terminal(reader_a)
            assert resultado_a["type"] == "result"
            writer_a.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_desconexion_durante_la_ejecucion_revoca_el_grupo_y_libera_el_cupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader_a, writer_a = await _conectar(puerto)
            await _enviar(writer_a, file=_archivo(tmp_path, "a.log"))
            acc_a = await _leer(reader_a)
            job_a = acc_a["job_id"]
            await _leer(reader_a)  # started: el grupo ya se publicó

            writer_a.close()
            await asyncio.sleep(0.2)  # darle tiempo a la tarea de desconexión

            assert control.grupos[job_a].results[0].revocado is True
            assert job_a not in coord._jobs

            # El cupo (max_jobs=1) debe haberse liberado: otro pedido se admite directo.
            reader_b, writer_b = await _conectar(puerto)
            await _enviar(writer_b, file=_archivo(tmp_path, "b.log"))
            acc_b = await _leer(reader_b)
            assert acc_b["state"] == "admitted"

            control.liberar(acc_b["job_id"])
            resultado_b = await _leer_hasta_terminal(reader_b)
            assert resultado_b["type"] == "result"
            writer_b.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_pedido_invalido_no_crea_job_ni_consume_cupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            writer.write(protocolo.codificar({"version": 1, "type": "analyze", "query": "no-existe"}))
            await writer.drain()
            error = await _leer(reader)
            assert error["type"] == "error"
            assert error["job_id"] is None
            assert error["code"] == "INVALID_REQUEST" or error["code"] == "UNSUPPORTED_QUERY"
            assert control.lanzados == []
            assert coord._jobs == {}
            writer.close()

            # El cupo sigue intacto: un pedido válido se admite directo.
            reader2, writer2 = await _conectar(puerto)
            await _enviar(writer2, file=_archivo(tmp_path))
            acc = await _leer(reader2)
            assert acc["state"] == "admitted"
            control.liberar(acc["job_id"])
            await _leer(reader2)  # started
            resultado = await _leer_hasta_terminal(reader2)
            assert resultado["type"] == "result"
            writer2.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_archivo_vacio_no_publica_tareas_y_responde_informe_vacio(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, max_jobs=1)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path, "vacio.log", b""))
            acc = await _leer(reader)
            job_id = acc["job_id"]
            started = await _leer(reader)
            assert started["chunks"] == 0

            resultado = await _leer(reader)
            assert resultado["type"] == "result"
            assert resultado["chunks"] == 0
            assert resultado["workers"] == 0
            assert resultado["resultados"] == []
            assert job_id not in control.lanzados
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

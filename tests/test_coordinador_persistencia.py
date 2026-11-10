"""Integración de persistencia e IPC en el coordinador (issues #20/#21).

PROJECT_STATEMENT §9.2/§11, CU-16, CU-23; AC-12, AC-20. Reutiliza el mismo
patrón de fakes controlables por evento que `test_coordinador.py` (issue
#19): lo nuevo acá es exclusivamente el camino de persistencia -- qué se
encola, cuándo se cuenta y qué pasa si el indexador no está disponible --
no la admisión/timeout/cancelación ya cubiertos ahí.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from loghunter import coordinador, protocolo


class _TareaFallidaFalsa(Exception):
    pass


class _ResultadoFalso:
    def ready(self) -> bool:
        return True

    def successful(self) -> bool:
        return True

    def revoke(self) -> None:
        pass


class _GrupoFalso:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.results = [_ResultadoFalso()]


class _Control:
    def __init__(self) -> None:
        self._eventos: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _evento(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._eventos.setdefault(job_id, threading.Event())

    def liberar(self, job_id: str) -> None:
        self._evento(job_id).set()

    def lanzar_grupo(self, uri: str, tamano: int, chunk_bytes: int, query: str, job_id: str) -> _GrupoFalso:
        return _GrupoFalso(job_id)

    def reducir_resultados(self, grupo: _GrupoFalso, timeout: float | None = None, intervalo: float = 0.2) -> dict[str, Any]:
        self._evento(grupo.id).wait(timeout=timeout)
        return {
            "contador": {"10.0.0.1": 5, "10.0.0.2": 3, "10.0.0.3": 1},
            "lineas": 9,
            "invalidas": 0,
            "workers": 1,
        }


class _ColaFalsa:
    """Sustituye `multiprocessing.Queue` para observar qué se encoló."""

    def __init__(self) -> None:
        self.informes: list[Any] = []

    def put(self, item: Any) -> None:
        self.informes.append(item)


class _ColaRota:
    def put(self, item: Any) -> None:
        raise RuntimeError("cola rota (simulado)")


def _coordinador(control: _Control, **kwargs: Any) -> coordinador.Coordinador:
    base: dict[str, Any] = {"max_jobs": 2, "job_timeout": 5.0, "chunk_mb_default": 32}
    base.update(kwargs)
    return coordinador.Coordinador(
        lanzar_grupo=control.lanzar_grupo,
        reducir_resultados=control.reducir_resultados,
        tarea_fallida_exc=_TareaFallidaFalsa,
        **base,
    )


async def _iniciar_servidor(coord: coordinador.Coordinador) -> tuple[asyncio.Server, int]:
    servidor = await asyncio.start_server(coord.atender_cliente, host="127.0.0.1", port=0, limit=protocolo.LIMITE_PEDIDO)
    return servidor, servidor.sockets[0].getsockname()[1]


async def _conectar(puerto: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", puerto)


async def _enviar(writer: asyncio.StreamWriter, **campos: Any) -> None:
    pedido = {"version": 1, "type": "analyze", "query": "top-ips", **campos}
    writer.write(protocolo.codificar(pedido))
    await writer.drain()


async def _leer(reader: asyncio.StreamReader) -> dict[str, Any]:
    linea = await reader.readline()
    assert linea
    return __import__("json").loads(linea)


async def _leer_hasta_terminal(reader: asyncio.StreamReader) -> dict[str, Any]:
    while True:
        mensaje = await _leer(reader)
        if mensaje["type"] in ("result", "error"):
            return mensaje


def _archivo(tmp_path: Path, nombre: str = "a.log", contenido: bytes = b"x" * 64) -> str:
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    return f"local://{ruta}"


def test_job_exitoso_encola_el_agregado_completo_antes_del_top(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        cola = _ColaFalsa()
        coord = _coordinador(control, cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path), top=1)
            acc = await _leer(reader)
            job_id = acc["job_id"]
            await _leer(reader)  # started

            control.liberar(job_id)
            resultado = await _leer_hasta_terminal(reader)
            assert resultado["type"] == "result"
            assert resultado["persistencia"] == "encolada"
            # La vista del cliente respeta --top=1...
            assert len(resultado["resultados"]) == 1

            # ...pero lo encolado para el indexador conserva TODAS las claves.
            assert len(cola.informes) == 1
            informe = cola.informes[0]
            assert informe["job_id"] == job_id
            assert len(informe["resultados"]) == 3
            assert {r["clave"] for r in informe["resultados"]} == {
                "10.0.0.1", "10.0.0.2", "10.0.0.3",
            }
            assert informe["top"] == 1
            assert informe["lineas"] == 9

            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_sin_cola_configurada_la_persistencia_queda_no_configurada(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control)  # sin cola_informes: modo de prueba de jobs
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            await _leer(reader)  # started
            control.liberar(acc["job_id"])
            resultado = await _leer_hasta_terminal(reader)
            assert resultado["type"] == "result"
            assert resultado["persistencia"] == "no_configurada"
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_servicio_no_disponible_rechaza_pedidos_nuevos_sin_consumir_cupo(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        cola = _ColaFalsa()
        coord = _coordinador(control, max_jobs=1, cola_informes=cola)
        coord._servicio_disponible = False
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            error = await _leer(reader)
            assert error["type"] == "error"
            assert error["code"] == "SERVICE_UNAVAILABLE"
            assert coord._jobs == {}
            writer.close()

            # El cupo nunca se tocó: un pedido posterior con el servicio
            # restablecido se admite directo.
            coord._servicio_disponible = True
            reader2, writer2 = await _conectar(puerto)
            await _enviar(writer2, file=_archivo(tmp_path, "b.log"))
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


def test_indexador_caido_durante_el_job_falla_con_service_unavailable_sin_encolar(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        cola = _ColaFalsa()
        coord = _coordinador(control, cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            job_id = acc["job_id"]
            await _leer(reader)  # started

            # El supervisor detecta la caída del hijo mientras el reduce corre.
            coord._servicio_disponible = False
            control.liberar(job_id)

            error = await _leer_hasta_terminal(reader)
            assert error["type"] == "error"
            assert error["code"] == "SERVICE_UNAVAILABLE"
            assert cola.informes == []  # nunca se encoló sin consumidor
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_fallo_al_encolar_el_informe_produce_service_unavailable(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        coord = _coordinador(control, cola_informes=_ColaRota())
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            await _leer(reader)  # started
            control.liberar(acc["job_id"])
            error = await _leer_hasta_terminal(reader)
            assert error["type"] == "error"
            assert error["code"] == "SERVICE_UNAVAILABLE"
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_metricas_contabilizan_emision_y_lineas_validas(tmp_path: Path) -> None:
    async def _correr() -> None:
        control = _Control()
        cola = _ColaFalsa()
        coord = _coordinador(control, cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            await _leer(reader)  # started
            control.liberar(acc["job_id"])
            resultado = await _leer_hasta_terminal(reader)
            assert resultado["type"] == "result"
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

        snapshot = coord._metricas.snapshot()
        assert snapshot["informes_emitidos"] == 1
        assert snapshot["lineas_validas"] == 9
        assert snapshot["jobs_activos"] == 0  # el cupo se liberó al terminar

    asyncio.run(_correr())


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

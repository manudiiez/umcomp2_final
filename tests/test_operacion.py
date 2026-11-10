"""Señales y apagado ordenado del coordinador (issue #22).

PROJECT_STATEMENT §13, CU-18, CU-19, CU-23; AC-13, AC-20, ADR-0009. Las
señales se entregan de verdad (`os.kill` al propio proceso): es la única
forma de probar que el handler de `asyncio.loop.add_signal_handler` quedó
bien instalado y no bloquea, no solo que `_apagar`/`_emitir_snapshot` hacen
lo correcto si se los llama directamente.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import signal
import threading
from pathlib import Path
from typing import Any

from loghunter import coordinador, indexador, ipc, protocolo


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


class _ControlBloqueante:
    """El reduce no termina solo: hace falta cancelar el job (apagado).

    El bloqueo real del hilo se acota a una fracción de segundo -- no al
    `timeout` del job -- porque cancelar la tarea asyncio que espera un
    `to_thread` no interrumpe el hilo del *executor* ya en ejecución; sin
    este tope, el cierre de `asyncio.run()` esperaría al hilo real hasta el
    deadline completo del job en cada prueba.
    """

    def lanzar_grupo(self, uri: str, tamano: int, chunk_bytes: int, query: str, job_id: str) -> _GrupoFalso:
        return _GrupoFalso(job_id)

    def reducir_resultados(self, grupo: _GrupoFalso, timeout: float | None = None, intervalo: float = 0.2) -> dict[str, Any]:
        threading.Event().wait(timeout=0.5)
        raise TimeoutError("nunca debería llegar: el job se cancela o la prueba termina antes")


class _ColaFalsa:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def put(self, item: Any) -> None:
        self.items.append(item)

    def close(self) -> None:
        pass


def _coordinador(**kwargs: Any) -> coordinador.Coordinador:
    control = _ControlBloqueante()
    base: dict[str, Any] = {"max_jobs": 2, "job_timeout": 30.0, "chunk_mb_default": 32}
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
    pedido = {"version": 1, "type": "analyze", "query": "status", **campos}
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


def test_sigterm_real_notifica_shutdown_drena_y_resuelve_el_cierre(tmp_path: Path) -> None:
    async def _correr() -> None:
        cola = _ColaFalsa()
        coord = _coordinador(cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            await _leer(reader)  # accepted
            await _leer(reader)  # started: el job queda "corriendo" y bloqueado

            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))

            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(coord.esperar_cierre(), timeout=5)

            error = await _leer_hasta_terminal(reader)
            assert error["type"] == "error"
            assert error["code"] == "SERVER_SHUTDOWN"

            # Se detuvo la admisión y se colocó el centinela tras lo encolado.
            assert cola.items[-1] is None
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_apagado_es_idempotente_ante_doble_senal(tmp_path: Path) -> None:
    async def _correr() -> None:
        cola = _ColaFalsa()
        coord = _coordinador(cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
            os.kill(os.getpid(), signal.SIGINT)
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(coord.esperar_cierre(), timeout=5)
            # Un solo centinela, no uno por señal recibida.
            assert cola.items.count(None) == 1
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


class _EscritorFalso:
    """Captura lo escrito sin abrir un socket real."""

    def __init__(self) -> None:
        self.bytes_escritos = bytearray()

    def write(self, data: bytes) -> None:
        self.bytes_escritos += data

    async def drain(self) -> None:
        pass

    def mensajes(self) -> list[dict[str, Any]]:
        import json

        return [json.loads(linea) for linea in self.bytes_escritos.splitlines() if linea]


def test_pedido_tras_el_cierre_recibe_server_shutdown_sin_admitirse(tmp_path: Path) -> None:
    """Una conexión que entra en la ventana angosta entre la señal y el cierre
    efectivo del socket no debe admitirse: `_procesar_job` corta apenas ve
    `_cerrando`, antes de tocar el semáforo."""

    async def _correr() -> None:
        coord = _coordinador(cola_informes=_ColaFalsa())
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
            await coord._apagar()

            job = coordinador.Job(
                id="job-tardio", file=_archivo(tmp_path), query="status", top=10, chunk_mb=32
            )
            escritor = _EscritorFalso()
            await coord._procesar_job(job, escritor)  # type: ignore[arg-type]

            mensajes = escritor.mensajes()
            assert len(mensajes) == 1
            assert mensajes[0]["type"] == "error"
            assert mensajes[0]["code"] == "SERVER_SHUTDOWN"
            assert coord._jobs == {}
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_sigusr1_real_emite_snapshot_sin_interrumpir_al_cliente(tmp_path: Path, caplog) -> None:
    async def _correr() -> None:
        cola = _ColaFalsa()
        coord = _coordinador(cola_informes=cola)
        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            acc = await _leer(reader)
            await _leer(reader)  # started

            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))

            with caplog.at_level(logging.INFO, logger="loghunter.coordinador"):
                os.kill(os.getpid(), signal.SIGUSR1)
                await asyncio.sleep(0.2)  # darle tiempo al handler agendado

            assert any(acc["job_id"] in r.message for r in caplog.records)
            assert any("running" in r.message for r in caplog.records)

            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


def test_supervisor_detecta_caida_real_del_indexador_y_bloquea_admision(tmp_path: Path) -> None:
    async def _correr() -> None:
        coord = _coordinador(cola_informes=_ColaFalsa())

        # Proceso hijo real que ya terminó: una caída real del indexador,
        # construida con el propio target de producción (`indexador.ejecutar`)
        # en vez de inventar uno nuevo para la prueba.
        cola_real: multiprocessing.Queue = multiprocessing.Queue()
        cola_real.put(None)  # centinela: el proceso sale solo
        proceso = multiprocessing.Process(
            target=indexador.ejecutar,
            args=(cola_real, str(tmp_path / "informes.sqlite"), ipc.Metricas()),
        )
        proceso.start()
        proceso.join(timeout=5)
        assert not proceso.is_alive()
        coord._proceso_indexador = proceso

        tarea = asyncio.ensure_future(coord._supervisar_indexador())
        try:
            await asyncio.sleep(coordinador._INTERVALO_SUPERVISION + 0.5)
        finally:
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)

        assert coord._servicio_disponible is False

        servidor, puerto = await _iniciar_servidor(coord)
        try:
            reader, writer = await _conectar(puerto)
            await _enviar(writer, file=_archivo(tmp_path))
            error = await _leer(reader)
            assert error["type"] == "error"
            assert error["code"] == "SERVICE_UNAVAILABLE"
            writer.close()
        finally:
            servidor.close()
            await servidor.wait_closed()

    asyncio.run(_correr())


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

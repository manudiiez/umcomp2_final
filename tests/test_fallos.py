"""Recuperación y errores aislados con infraestructura real (issue #23).

PROJECT_STATEMENT §§9.2, 9.3, 11, 13; CU-13 a CU-16, CU-22, CU-23;
AC-09, AC-10, AC-11, AC-19, AC-20.

`test_coordinador.py`, `test_coordinador_persistencia.py` y `test_operacion.py`
ya cubren con dobles (`lanzar_grupo`/`reducir_resultados` falsos) la lógica de
timeout, fallo de tarea, indexador caído y desconexión: esa cobertura no se
repite acá. Este archivo agrega lo que los dobles no pueden demostrar: el
**coordinador real** (con `loghunter.analisis` real, su indexador hijo real y,
donde aplica, Celery workers reales) atravesando fallos genuinos de
infraestructura (Redis/MinIO caídos, un ejecutor congelado, una tarea que
falla de verdad) sin que un job con fallo definitivo bloquee a otro
concurrente. Como `tests/test_integracion_distribucion.py`, se saltan solos
si no hay Redis/MinIO reales en este host en vez de simularlos (ver
INSTALL.md). La pérdida real de un ejecutor a mitad de job (AC-09) se
verificó manualmente en vez de automatizarse: ver la nota junto a
`doc/VERIFICACION_FALLOS.md` más abajo.

Estas pruebas detienen y reinician temporalmente los contenedores
`loghunter-redis`/`loghunter-minio` para inyectar fallos reales; cada una los
restaura en su `finally` antes de salir, con una espera activa a que vuelvan a
responder, para no afectar al resto de la suite ni al entorno del operador.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from loghunter import coordinador, protocolo
from loghunter.generar import escribir_log

_REDIS_CONTENEDOR = "loghunter-redis"
_MINIO_CONTENEDOR = "loghunter-minio"
_S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
_ENV_WORKER_BASE = {
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/1",
    "S3_ENDPOINT": _S3_ENDPOINT,
    "AWS_ACCESS_KEY_ID": "minioadmin",
    "AWS_SECRET_ACCESS_KEY": "minioadmin",
    "AWS_DEFAULT_REGION": "us-east-1",
}


def _puerto_abierto(host: str, puerto: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _minio_disponible(endpoint: str = _S3_ENDPOINT) -> bool:
    try:
        with urlopen(f"{endpoint}/minio/health/live", timeout=2) as respuesta:
            return respuesta.status == 200
    except (URLError, OSError, ValueError):
        return False


def _infraestructura_disponible() -> bool:
    return _puerto_abierto("localhost", 6379) and _minio_disponible()


requiere_infraestructura_real = pytest.mark.skipif(
    not _infraestructura_disponible(),
    reason=(
        "requiere Redis y MinIO reales en localhost, contenedores "
        f"'{_REDIS_CONTENEDOR}'/'{_MINIO_CONTENEDOR}' (ver INSTALL.md); "
        "no se simulan con dobles"
    ),
)


# -- Manejo de contenedores (fallos de infraestructura reales) --------------


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20)


def _esperar(predicado: Any, plazo: float, intervalo: float = 0.3) -> bool:
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        if predicado():
            return True
        time.sleep(intervalo)
    return predicado()


class _redis_caido:
    """Context manager: detiene Redis y lo restaura garantizado al salir."""

    def __enter__(self) -> "_redis_caido":
        _docker("stop", "-t", "1", _REDIS_CONTENEDOR)
        assert _esperar(lambda: not _puerto_abierto("localhost", 6379), 5), (
            "Redis debía quedar inalcanzable tras 'docker stop'"
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        _docker("start", _REDIS_CONTENEDOR)
        assert _esperar(lambda: _puerto_abierto("localhost", 6379), 15), (
            "Redis no volvió a responder tras 'docker start'; entorno degradado"
        )


class _minio_caido:
    def __enter__(self) -> "_minio_caido":
        _docker("stop", "-t", "1", _MINIO_CONTENEDOR)
        assert _esperar(lambda: not _minio_disponible(), 5), (
            "MinIO debía quedar inalcanzable tras 'docker stop'"
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        _docker("start", _MINIO_CONTENEDOR)
        assert _esperar(_minio_disponible, 15), (
            "MinIO no volvió a responder tras 'docker start'; entorno degradado"
        )


# -- Workers Celery reales, lanzados y apagados por la prueba ----------------


def _lanzar_worker(nombre: str, visibility_timeout: int | None = None) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env.update(_ENV_WORKER_BASE)
    if visibility_timeout is not None:
        env["LOGHUNTER_VISIBILITY_TIMEOUT"] = str(visibility_timeout)
    return subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "loghunter.tasks", "worker",
            "--loglevel=WARNING", "-c", "1", "-n", f"{nombre}@%h",
            "--without-gossip", "--without-mingle", "--without-heartbeat",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _esperar_workers(minimo: int, plazo: float = 20.0) -> None:
    from loghunter.tasks import app

    def _listos() -> bool:
        try:
            activos = app.control.inspect(timeout=1.0).ping()
        except Exception:
            return False
        return bool(activos) and len(activos) >= minimo

    assert _esperar(_listos, plazo, intervalo=0.5), (
        f"no se detectaron {minimo} worker(s) Celery reales a tiempo"
    )


def _detener_worker(proceso: subprocess.Popen[bytes], forzado: bool = False) -> None:
    if proceso.poll() is not None:
        return
    proceso.send_signal(signal.SIGKILL if forzado else signal.SIGTERM)
    try:
        proceso.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proceso.kill()
        proceso.wait(timeout=5)


# -- Arnés mínimo de coordinador real + cliente TCP --------------------------


async def _iniciar_servidor(coord: coordinador.Coordinador) -> tuple[asyncio.Server, int]:
    servidor = await asyncio.start_server(
        coord.atender_cliente, host="127.0.0.1", port=0, limit=protocolo.LIMITE_PEDIDO
    )
    return servidor, servidor.sockets[0].getsockname()[1]


async def _pedido(puerto: int, *, file: str, query: str = "status", chunk_mb: int = 1) -> tuple[
    asyncio.StreamReader, asyncio.StreamWriter
]:
    reader, writer = await asyncio.open_connection("127.0.0.1", puerto)
    mensaje = {
        "version": protocolo.VERSION, "type": "analyze",
        "file": file, "query": query, "top": 10, "chunk_mb": chunk_mb,
    }
    writer.write(protocolo.codificar(mensaje))
    await writer.drain()
    return reader, writer


async def _leer(reader: asyncio.StreamReader) -> dict[str, Any]:
    linea = await reader.readline()
    assert linea, "el coordinador cerró la conexión sin responder"
    return json.loads(linea)


async def _leer_hasta_terminal(reader: asyncio.StreamReader, plazo: float) -> dict[str, Any]:
    async def _correr() -> dict[str, Any]:
        while True:
            mensaje = await _leer(reader)
            if mensaje["type"] in ("result", "error"):
                return mensaje

    return await asyncio.wait_for(_correr(), timeout=plazo)


def _archivo_local(tmp_path: Path, size_mb: int = 24, ips: int = 30, corrupt_pct: int = 2, seed: int = 7) -> str:
    ruta = tmp_path / f"fallos-{seed}.log"
    escribir_log(destino=str(ruta), size_mb=size_mb, ips=ips, corrupt_pct=corrupt_pct, seed=seed)
    return str(ruta)


# AC-09 (pérdida de ejecutor a mitad de un job real, a través del
# coordinador/cliente completo, no solo de `analisis` como en el milestone 3)
# se verificó manualmente, no en esta suite: la recuperación real del
# transporte Redis de Celery tarda muchos segundos más que el
# `visibility_timeout` configurado (~98 s con `visibility_timeout=8`, igual
# de lejos del valor nominal que los ~200 s con `visibility_timeout=10`
# documentados en `doc/VERIFICACION_DISTRIBUCION.md` para el milestone 3) y
# convertirla en una prueba automática de `pytest` haría la suite
# excesivamente lenta sin agregar certeza: es la misma razón por la que
# aquella verificación tampoco está en `tests/test_integracion_distribucion.py`.
# El procedimiento reproducible y su salida real están documentados en
# `doc/VERIFICACION_FALLOS.md`.


# -----------------------------------------------------------------------
# AC-10: fallo permanente de una tarea no espera el 100% y no cancela a
# otro job concurrente.
# -----------------------------------------------------------------------


@requiere_infraestructura_real
def test_fallo_definitivo_de_tarea_no_afecta_a_un_job_concurrente_sano(tmp_path: Path) -> None:
    """Job 1 pierde el permiso de lectura de su archivo justo después de que
    el coordinador calculó los tramos (antes de publicarlos): el worker real
    falla de verdad al intentar leer su tramo, sin ningún doble que simule el
    fallo. Job 2, sano y concurrente en el mismo coordinador, no debe verse
    afectado (AC-10: "otro job no se cancela arbitrariamente")."""
    ruta_mala = _archivo_local(tmp_path, size_mb=2, seed=10)
    ruta_buena = _archivo_local(tmp_path, size_mb=1, seed=11)

    worker = _lanzar_worker("unico", visibility_timeout=30)
    try:
        _esperar_workers(1)

        async def _consumir_bueno(reader: asyncio.StreamReader) -> dict[str, Any]:
            await _leer(reader)  # accepted
            await _leer(reader)  # started
            return await _leer_hasta_terminal(reader, plazo=30)

        async def _correr() -> tuple[dict[str, Any], dict[str, Any]]:
            coord = coordinador.Coordinador(max_jobs=2, job_timeout=40, chunk_mb_default=1)
            servidor, puerto = await _iniciar_servidor(coord)
            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
            try:
                reader_malo, writer_malo = await _pedido(
                    puerto, file=f"local://{ruta_mala}", query="status", chunk_mb=1
                )
                await _leer(reader_malo)  # accepted
                await _leer(reader_malo)  # started: tramos calculados, aún sin publicar
                os.chmod(ruta_mala, 0o000)

                reader_bueno, writer_bueno = await _pedido(
                    puerto, file=f"local://{ruta_buena}", query="status", chunk_mb=1
                )
                try:
                    error_malo, resultado_bueno = await asyncio.gather(
                        _leer_hasta_terminal(reader_malo, plazo=30),
                        _consumir_bueno(reader_bueno),
                    )
                finally:
                    os.chmod(ruta_mala, 0o644)
                writer_malo.close()
                writer_bueno.close()
                return error_malo, resultado_bueno
            finally:
                await coord._apagar()
                servidor.close()
                await servidor.wait_closed()

        error_malo, resultado_bueno = asyncio.run(_correr())
    finally:
        os.chmod(ruta_mala, 0o644)
        _detener_worker(worker)

    assert error_malo["type"] == "error"
    assert error_malo["code"] == "TASK_FAILED"
    assert resultado_bueno["type"] == "result", resultado_bueno


# -----------------------------------------------------------------------
# AC-10: caída permanente de MinIO -> error finito (STORAGE_UNAVAILABLE),
# el coordinador no queda bloqueado para otros pedidos.
# -----------------------------------------------------------------------


@requiere_infraestructura_real
def test_minio_caido_produce_error_finito_y_el_coordinador_sigue_atendiendo(tmp_path: Path) -> None:
    # Archivo vacío a propósito: esta prueba verifica que el coordinador
    # sigue vivo y atendiendo tras la caída de MinIO, no el reduce
    # distribuido (eso ya lo cubren otras pruebas); un archivo vacío no
    # publica tareas y no requiere un worker Celery real arriba.
    ruta_vacio = tmp_path / "vacio.log"
    ruta_vacio.write_bytes(b"")

    async def _correr() -> tuple[dict[str, Any], dict[str, Any]]:
        coord = coordinador.Coordinador(max_jobs=2, job_timeout=30, chunk_mb_default=1)
        servidor, puerto = await _iniciar_servidor(coord)
        coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
        try:
            with _minio_caido():
                reader, writer = await _pedido(puerto, file="s3://cualquier-bucket/x.log", query="status")
                error_s3 = await _leer_hasta_terminal(reader, plazo=10)
                writer.close()

            # Con MinIO ya restaurado, el coordinador sigue vivo y atiende
            # un pedido nuevo sin reiniciarse.
            reader2, writer2 = await _pedido(puerto, file=f"local://{ruta_vacio}", query="status")
            await _leer(reader2)
            resultado_local = await _leer_hasta_terminal(reader2, plazo=10)
            writer2.close()
            return error_s3, resultado_local
        finally:
            await coord._apagar()
            servidor.close()
            await servidor.wait_closed()

    error_s3, resultado_local = asyncio.run(_correr())

    assert error_s3["type"] == "error"
    assert error_s3["code"] == "STORAGE_UNAVAILABLE"
    assert "minioadmin" not in json.dumps(error_s3), "no se deben filtrar credenciales en el mensaje de error"
    assert resultado_local["type"] == "result"


# -----------------------------------------------------------------------
# AC-10: caída de Redis al publicar -> BROKER_UNAVAILABLE finito.
# -----------------------------------------------------------------------


@requiere_infraestructura_real
def test_redis_caido_al_publicar_produce_broker_unavailable(tmp_path: Path) -> None:
    ruta_local = _archivo_local(tmp_path, size_mb=1, seed=33)

    async def _correr() -> dict[str, Any]:
        coord = coordinador.Coordinador(max_jobs=1, job_timeout=30, chunk_mb_default=1)
        servidor, puerto = await _iniciar_servidor(coord)
        coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
        try:
            with _redis_caido():
                reader, writer = await _pedido(puerto, file=f"local://{ruta_local}", query="status")
                await _leer(reader)  # accepted
                error = await _leer_hasta_terminal(reader, plazo=20)
                writer.close()
                return error
        finally:
            await coord._apagar()
            servidor.close()
            await servidor.wait_closed()

    error = asyncio.run(_correr())

    assert error["type"] == "error"
    assert error["code"] == "BROKER_UNAVAILABLE"


# -----------------------------------------------------------------------
# AC-11: timeout real de un job (ejecutor real pero congelado con SIGSTOP)
# revoca solo sus pendientes; otro job concurrente no se cancela.
# -----------------------------------------------------------------------


@requiere_infraestructura_real
def test_timeout_real_no_cancela_un_job_concurrente_y_libera_cupo(tmp_path: Path) -> None:
    ruta = _archivo_local(tmp_path, size_mb=4, seed=44)
    ruta_vacio = tmp_path / "vacio.log"
    ruta_vacio.write_bytes(b"")

    worker = _lanzar_worker("congelable", visibility_timeout=60)
    try:
        _esperar_workers(1)
        assert worker.pid is not None

        async def _correr() -> tuple[dict[str, Any], dict[str, Any]]:
            coord = coordinador.Coordinador(max_jobs=2, job_timeout=5, chunk_mb_default=1)
            servidor, puerto = await _iniciar_servidor(coord)
            coord.iniciar_servicios(servidor, str(tmp_path / "informes.sqlite"))
            try:
                # Congelar el único worker real (SIGSTOP, no SIGKILL): sus
                # tareas quedan pendientes de verdad, como un ejecutor que
                # dejó de responder sin haber caído.
                os.kill(worker.pid, signal.SIGSTOP)
                try:
                    reader_lento, writer_lento = await _pedido(
                        puerto, file=f"local://{ruta}", query="status", chunk_mb=1
                    )
                    await _leer(reader_lento)  # accepted
                    await _leer(reader_lento)  # started

                    # Job 2, concurrente, sin tareas que publicar: debe
                    # resolverse igual aunque el worker esté congelado.
                    reader_vacio, writer_vacio = await _pedido(
                        puerto, file=f"local://{ruta_vacio}", query="status"
                    )
                    await _leer(reader_vacio)
                    resultado_vacio = await _leer_hasta_terminal(reader_vacio, plazo=10)
                    writer_vacio.close()

                    error_timeout = await _leer_hasta_terminal(reader_lento, plazo=15)
                    writer_lento.close()
                    return error_timeout, resultado_vacio
                finally:
                    os.kill(worker.pid, signal.SIGCONT)
            finally:
                await coord._apagar()
                servidor.close()
                await servidor.wait_closed()

        error_timeout, resultado_vacio = asyncio.run(_correr())
    finally:
        try:
            if worker.pid is not None:
                os.kill(worker.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        _detener_worker(worker, forzado=True)

    assert resultado_vacio["type"] == "result", resultado_vacio
    assert error_timeout["type"] == "error"
    assert error_timeout["code"] == "JOB_TIMEOUT"


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

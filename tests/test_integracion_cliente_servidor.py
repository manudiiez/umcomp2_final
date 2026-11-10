"""Extremo a extremo con infraestructura real: cliente, coordinador y Celery
(complemento de los issues #16/#17/#19).

A diferencia de `test_coordinador.py`/`test_cliente.py` (que inyectan
`lanzar_grupo`/`reducir_resultados` fakes para aislar la capa de jobs), esta
prueba usa el `Coordinador` con `loghunter.analisis` real: publica el grupo
en Redis, lo procesa un worker Celery real y el cliente CLI real consume el
informe por TCP. Si Redis o un worker no están alcanzables, se salta con un
motivo explícito en vez de simularlos (mismo criterio que
`test_integracion_distribucion.py`, PROJECT_STATEMENT §15: "los mocks por sí
solos no demuestran distribución").
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from loghunter import coordinador, protocolo

_RAIZ = Path(__file__).resolve().parent.parent


def _puerto_abierto(host: str, puerto: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _worker_disponible() -> bool:
    if not _puerto_abierto("localhost", 6379):
        return False
    try:
        from loghunter.tasks import app

        respuestas = app.control.ping(timeout=2.0)
    except Exception:
        return False
    return bool(respuestas)


pytestmark = pytest.mark.skipif(
    not _worker_disponible(),
    reason="requiere Redis y al menos un worker Celery real alcanzables (ver INSTALL.md)",
)


def _lanzar_coordinador_real(max_jobs: int = 2, job_timeout: float = 30.0) -> tuple[int, threading.Thread, threading.Event]:
    puerto_listo = threading.Event()
    detener = threading.Event()
    puerto_holder: list[int] = []

    def _correr() -> None:
        async def _servir() -> None:
            coord = coordinador.Coordinador(
                max_jobs=max_jobs, job_timeout=job_timeout, chunk_mb_default=32
            )
            servidor = await asyncio.start_server(
                coord.atender_cliente, host="127.0.0.1", port=0, limit=protocolo.LIMITE_PEDIDO
            )
            puerto_holder.append(servidor.sockets[0].getsockname()[1])
            puerto_listo.set()
            async with servidor:
                await asyncio.get_event_loop().run_in_executor(None, detener.wait)
                servidor.close()

        asyncio.run(_servir())

    hilo = threading.Thread(target=_correr, daemon=True)
    hilo.start()
    puerto_listo.wait(timeout=5)
    return puerto_holder[0], hilo, detener


def test_analisis_completo_con_redis_y_worker_reales(tmp_path: Path) -> None:
    """CU-01/CU-06 con infraestructura real: AC-01, AC-07, AC-14."""
    contenido = (
        b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"\n'
        b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"\n'
        b'10.0.0.1 - - [20/Oct/2026:14:10:00 -0300] "GET /a HTTP/1.1" 200 50 "-" "demo"\n'
        b"registro corrupto\n"
    )
    archivo = tmp_path / "access.log"
    archivo.write_bytes(contenido)

    puerto, hilo, detener = _lanzar_coordinador_real()
    try:
        resultado = subprocess.run(
            [
                sys.executable, "-m", "loghunter.cliente",
                "-i", "127.0.0.1", "-p", str(puerto),
                "-f", str(archivo), "-q", "top-ips", "--chunk-mb", "1", "--raw",
            ],
            capture_output=True, text=True, cwd=str(_RAIZ), timeout=60,
        )
        assert resultado.returncode == 0, resultado.stderr
        informe = json.loads(resultado.stdout)
        assert informe["type"] == "result"
        assert informe["lineas"] == 3
        assert informe["invalidas"] == 1
        assert informe["chunks"] == 1  # archivo pequeño: un solo tramo con chunk_mb=1
        assert {"clave": "10.0.0.1", "valor": 2} in informe["resultados"]
        assert {"clave": "10.0.0.2", "valor": 1} in informe["resultados"]
        assert informe["workers"] >= 1
    finally:
        detener.set()
        hilo.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

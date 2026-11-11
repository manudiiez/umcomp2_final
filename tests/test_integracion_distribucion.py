"""Verificación de equivalencia local/S3 con infraestructura real (issue #14).

PROJECT_STATEMENT §§8.3, 9.3; CU-06, CU-07, CU-13; AC-02, AC-07, AC-08, AC-09.
A diferencia de `tests/test_tasks.py`, `tests/test_lector_s3.py` y
`tests/test_analisis.py` (que usan dobles y no requieren nada externo), estas
pruebas necesitan Redis, MinIO y al menos un worker Celery real alcanzables
por las variables de entorno estándar (ver INSTALL.md): si no están
disponibles, se saltan con un motivo explícito en vez de simularlos. El
procedimiento manual con un worker en otro contenedor (sin acceso al
filesystem del log) y la simulación de pérdida de ejecutor, ambos ejecutados
de verdad, están documentados con su salida real en
`doc/VERIFICACION_DISTRIBUCION.md`.

Las pruebas de backend `local://` de este archivo asumen que **todos** los
workers conectados a la cola comparten el filesystem con el archivo (CU-06,
PROJECT_STATEMENT §8.3): si en algún momento hay además un worker remoto sin
esa ruta (como el del escenario "otro host" de VERIFICACION_DISTRIBUCION.md)
conectado a la misma cola, Celery puede despachar un tramo `local://` ahí y
la tarea falla con `FileNotFoundError` — correctamente, no es un error de
`analisis.py`. Se confirmó este comportamiento al ejecutar esta suite junto
al worker remoto todavía activo; se detuvo ese contenedor antes de la corrida
que se documenta como aprobada.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from loghunter.analisis import lanzar_grupo, nuevo_job_id, reducir_resultados
from loghunter.consultas import calcular_referencia
from loghunter.generar import escribir_log

_BUCKET = "logs-integracion"


def _puerto_abierto(host: str, puerto: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def _minio_disponible(endpoint: str) -> bool:
    try:
        with urlopen(f"{endpoint}/minio/health/live", timeout=2) as respuesta:
            return respuesta.status == 200
    except (URLError, OSError, ValueError):
        return False


def _worker_disponible() -> bool:
    """Hay al menos un worker Celery real respondiendo si `ping()` no queda vacío."""
    from loghunter.tasks import app

    try:
        activos = app.control.inspect(timeout=1.0).ping()
    except Exception:
        return False
    return bool(activos)


def _infraestructura_disponible() -> bool:
    broker_host = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    puerto_redis = 6379
    if "localhost" not in broker_host and "127.0.0.1" not in broker_host:
        return False  # esta suite asume Redis/MinIO accesibles en este host (ver INSTALL.md)
    endpoint_s3 = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
    return (
        _puerto_abierto("localhost", puerto_redis)
        and _minio_disponible(endpoint_s3)
        and _worker_disponible()
    )


requiere_infraestructura_real = pytest.mark.skipif(
    not _infraestructura_disponible(),
    reason=(
        "requiere Redis, MinIO y un worker Celery reales en localhost "
        "(ver INSTALL.md, sección 'Servicios externos'); no se simulan con dobles"
    ),
)


@pytest.fixture(scope="module")
def log_local(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directorio = tmp_path_factory.mktemp("distribucion")
    ruta = directorio / "access.log"
    escribir_log(destino=str(ruta), size_mb=2, ips=40, corrupt_pct=3, seed=4242)
    return ruta


@pytest.fixture(scope="module")
def uri_s3(log_local: Path) -> str:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    cliente = boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        cliente.create_bucket(Bucket=_BUCKET)
    except ClientError:
        pass  # el bucket ya existe de una corrida anterior de esta suite
    cliente.upload_file(str(log_local), _BUCKET, "access.log")
    cliente.close()
    return f"s3://{_BUCKET}/access.log"


@requiere_infraestructura_real
@pytest.mark.parametrize("chunk_mb", [1, 4])
@pytest.mark.parametrize("query", ["top-ips", "status", "urls", "traffic"])
def test_backend_local_distribuido_coincide_con_la_referencia(
    log_local: Path, chunk_mb: int, query: str
) -> None:
    referencia, validas, invalidas = calcular_referencia(log_local, query)
    uri = f"local://{log_local.resolve()}"
    grupo = lanzar_grupo(uri, log_local.stat().st_size, chunk_mb * 2**20, query, nuevo_job_id())

    agregado = reducir_resultados(grupo, timeout=60)

    assert agregado["lineas"] == validas
    assert agregado["invalidas"] == invalidas
    assert agregado["contador"] == dict(referencia)


@requiere_infraestructura_real
@pytest.mark.parametrize("chunk_mb", [1, 4])
def test_backend_s3_distribuido_coincide_con_la_referencia_y_con_local(
    log_local: Path, uri_s3: str, chunk_mb: int
) -> None:
    referencia, validas, invalidas = calcular_referencia(log_local, "top-ips")
    tamano = log_local.stat().st_size
    grupo = lanzar_grupo(uri_s3, tamano, chunk_mb * 2**20, "top-ips", nuevo_job_id())

    agregado = reducir_resultados(grupo, timeout=60)

    assert agregado["lineas"] == validas
    assert agregado["invalidas"] == invalidas
    assert agregado["contador"] == dict(referencia)


@requiere_infraestructura_real
def test_archivo_vacio_no_publica_tareas_y_no_espera_un_grupo(log_local: Path) -> None:
    grupo = lanzar_grupo("local:///dev/null", 0, 32 * 2**20, "status", nuevo_job_id())
    assert reducir_resultados(grupo, timeout=5) == {
        "contador": {},
        "lineas": 0,
        "invalidas": 0,
        "workers": 0,
    }

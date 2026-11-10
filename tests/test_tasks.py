"""Pruebas de la tarea Celery map_chunk (issue #10): PROJECT_STATEMENT §9.3 y ADR-0003.

Se invoca la tarea directamente con `.run()` (sin broker): alcanza para
comprobar el contrato de salida y que reutiliza `workers.map_chunk` sin
cambios. El recorrido con Redis/Celery real (grupo, redelivery, otro host)
es responsabilidad del issue #14, con infraestructura real.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from loghunter import workers
from loghunter.tasks import configuracion_desde_entorno, crear_app, map_chunk

CONTENIDO = (
    b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"\n'
    b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"\n'
    b"registro corrupto\n"
)


def _escribir_log(tmp_path: Path) -> str:
    archivo = tmp_path / "access.log"
    archivo.write_bytes(CONTENIDO)
    return str(archivo)


def test_map_chunk_adapta_el_resultado_de_workers_sin_cambiar_el_computo(
    tmp_path: Path,
) -> None:
    uri = _escribir_log(tmp_path)
    esperado = workers.map_chunk(uri, 0, len(CONTENIDO), "status")

    resultado = map_chunk.run(uri, 0, len(CONTENIDO), "status", "job-1")

    assert resultado["contador"] == esperado["contador"]
    assert resultado["lineas"] == esperado["validas"]
    assert resultado["invalidas"] == esperado["invalidas"]
    assert resultado["worker"] == {"hostname": socket.gethostname(), "pid": os.getpid()}


def test_map_chunk_es_idempotente(tmp_path: Path) -> None:
    uri = _escribir_log(tmp_path)
    primero = map_chunk.run(uri, 0, len(CONTENIDO), "top-ips", "job-2")
    segundo = map_chunk.run(uri, 0, len(CONTENIDO), "top-ips", "job-2")
    assert primero == segundo


def test_job_id_no_afecta_el_computo(tmp_path: Path) -> None:
    uri = _escribir_log(tmp_path)
    a = map_chunk.run(uri, 0, len(CONTENIDO), "urls", "job-a")
    b = map_chunk.run(uri, 0, len(CONTENIDO), "urls", "job-b")
    assert a["contador"] == b["contador"]
    assert a["lineas"] == b["lineas"]
    assert a["invalidas"] == b["invalidas"]


def test_configuracion_desde_entorno_usa_defaults_de_project_statement(
    monkeypatch,
) -> None:
    for var in (
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "LOGHUNTER_TASK_TIME_LIMIT",
        "LOGHUNTER_VISIBILITY_TIMEOUT",
        "LOGHUNTER_RESULT_EXPIRES",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = configuracion_desde_entorno()

    assert cfg == {
        "broker_url": "redis://localhost:6379/0",
        "result_backend": "redis://localhost:6379/1",
        "task_time_limit": 120,
        "visibility_timeout": 180,
        "result_expires": 3600,
    }


def test_configuracion_desde_entorno_respeta_variables_explicitas(monkeypatch) -> None:
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://otrahost:6379/5")
    monkeypatch.setenv("LOGHUNTER_TASK_TIME_LIMIT", "45")

    cfg = configuracion_desde_entorno()

    assert cfg["broker_url"] == "redis://otrahost:6379/5"
    assert cfg["task_time_limit"] == 45


def test_crear_app_configura_confirmacion_tardia_y_rechazo_al_perder_worker() -> None:
    aplicacion = crear_app(
        {
            "broker_url": "redis://localhost:6379/0",
            "result_backend": "redis://localhost:6379/1",
            "task_time_limit": 120,
            "visibility_timeout": 180,
            "result_expires": 3600,
        }
    )

    assert aplicacion.conf.task_acks_late is True
    assert aplicacion.conf.task_reject_on_worker_lost is True
    assert aplicacion.conf.task_time_limit == 120
    assert aplicacion.conf.broker_transport_options == {"visibility_timeout": 180}
    assert aplicacion.conf.result_expires == 3600
    assert aplicacion.conf.task_serializer == "json"
    assert aplicacion.conf.result_serializer == "json"
    assert aplicacion.conf.accept_content == ["json"]

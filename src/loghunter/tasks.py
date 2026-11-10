"""Tareas Celery para procesar rangos del log en otros procesos (issue #10).

La app Celery se configura solo con variables de entorno (PROJECT_STATEMENT
§12.4, ADR-0003): broker y backend explícitos, `task_acks_late` y
`task_reject_on_worker_lost` para recuperar tareas de un ejecutor perdido,
`task_time_limit`, `visibility_timeout` del broker y retención de resultados.
`map_chunk` reutiliza `workers.map_chunk` sin cambios: solo adapta su
resultado al contrato de red de §9.3 y agrega la identidad del proceso que
lo ejecutó. No reintenta nada por sí misma: un error permanente debe
propagar y marcar la tarea `FAILURE`; la recuperación ante pérdida del
ejecutor la da la confirmación tardía del broker, no código de aplicación.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

from celery import Celery

from loghunter import workers

logger = logging.getLogger(__name__)


def _entero_env(nombre: str, default: int) -> int:
    valor = os.environ.get(nombre)
    return int(valor) if valor else default


def configuracion_desde_entorno() -> dict[str, Any]:
    """Configuración efectiva de Celery, leída de variables de entorno.

    Valores iniciales adoptados en PROJECT_STATEMENT §9.3/§12.4 como default
    cuando la variable no está definida.
    """
    return {
        "broker_url": os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        "result_backend": os.environ.get(
            "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
        ),
        "task_time_limit": _entero_env("LOGHUNTER_TASK_TIME_LIMIT", 120),
        "visibility_timeout": _entero_env("LOGHUNTER_VISIBILITY_TIMEOUT", 180),
        "result_expires": _entero_env("LOGHUNTER_RESULT_EXPIRES", 3600),
    }


def crear_app(config: dict[str, Any] | None = None) -> Celery:
    """Construye la app Celery de LogHunter con la configuración dada o del entorno."""
    cfg = config if config is not None else configuracion_desde_entorno()
    aplicacion = Celery(
        "loghunter", broker=cfg["broker_url"], backend=cfg["result_backend"]
    )
    aplicacion.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_time_limit=cfg["task_time_limit"],
        broker_transport_options={"visibility_timeout": cfg["visibility_timeout"]},
        result_expires=cfg["result_expires"],
    )
    return aplicacion


app = crear_app()


@app.task(name="loghunter.map_chunk", bind=True)
def map_chunk(
    self: Any, uri: str, inicio: int, fin: int, query: str, job_id: str
) -> dict[str, Any]:
    """Procesa el tramo `[inicio, fin)` de `uri` en este worker.

    Reutiliza `workers.map_chunk` sin cambios (puro e idempotente) y adapta
    su resultado al contrato mínimo de PROJECT_STATEMENT §9.3: `contador`,
    `lineas`, `invalidas` y la identidad `{hostname, pid}` del proceso que
    lo ejecutó. `job_id` solo identifica logs; no afecta el cómputo.
    """
    logger.info(
        "map_chunk job_id=%s task_id=%s uri=%s rango=[%d,%d) query=%s",
        job_id,
        self.request.id,
        uri,
        inicio,
        fin,
        query,
    )
    parcial = workers.map_chunk(uri, inicio, fin, query)
    return {
        "contador": parcial["contador"],
        "lineas": parcial["validas"],
        "invalidas": parcial["invalidas"],
        "worker": {"hostname": socket.gethostname(), "pid": os.getpid()},
    }


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.tasks no se ejecuta directamente; usar "
        "'celery -A loghunter.tasks worker'."
    )

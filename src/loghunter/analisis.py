"""Correlación de jobs y reduce de parciales distribuidos (issue #13).

Capa reutilizable por el futuro coordinador (milestone 4, con
`asyncio.to_thread`): no es el coordinador en sí, pero encapsula exactamente
lo que PROJECT_STATEMENT §9.2/§9.3 exige para lanzar un grupo de tareas y
reducir sus resultados, sin mezclar jobs ni esperar un 100% de éxitos cuando
ya hay un fallo definitivo. Las pruebas de extremo a extremo con Celery y
Redis reales están en el issue #14.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from celery.result import AsyncResult

from loghunter import workers
from loghunter.consultas import reducir
from loghunter.tasks import map_chunk


class TareaFallida(Exception):
    """Un tramo del job terminó con fallo definitivo; el grupo no se reduce."""


@dataclass(frozen=True, slots=True)
class Grupo:
    """Los `AsyncResult` de un job, correlacionados por `job_id`.

    No es `celery.result.GroupResult`: esa clase exige que cada resultado
    implemente el protocolo de promesas (`.then()`) para su barrera interna
    de "todos listos", que no necesitamos — `reducir_resultados` hace su
    propio sondeo por tarea para distinguir éxito/fallo sin esperar el
    100% (PROJECT_STATEMENT §9.2).
    """

    id: str
    results: list[AsyncResult] = field(default_factory=list)


def nuevo_job_id() -> str:
    """UUID hexadecimal para un pedido estructuralmente válido (PROJECT_STATEMENT §9.2)."""
    return uuid.uuid4().hex


def lanzar_grupo(uri: str, tamano: int, chunk_bytes: int, query: str, job_id: str) -> Grupo:
    """Publica una tarea `map_chunk` por tramo y devuelve su `Grupo`.

    Pre-genera un `task_id` por rango **antes** de publicar nada, para poder
    revocar lo ya publicado si una publicación falla a mitad de camino: no
    alcanza con asumir que ningún tramo llegó al broker (PROJECT_STATEMENT
    §9.3). Un archivo vacío no publica ningún grupo.
    """
    rangos = workers.calcular_rangos(tamano, chunk_bytes)
    if not rangos:
        return Grupo(id=job_id, results=[])

    task_ids = [nuevo_job_id() for _ in rangos]
    publicados: list[AsyncResult] = []
    try:
        for task_id, (inicio, fin) in zip(task_ids, rangos):
            publicados.append(
                map_chunk.apply_async(
                    args=(uri, inicio, fin, query, job_id), task_id=task_id
                )
            )
    except Exception:
        for resultado in publicados:
            resultado.revoke()
        raise

    return Grupo(id=job_id, results=publicados)


def reducir_resultados(
    grupo: Grupo, timeout: float | None = None, intervalo: float = 0.2
) -> dict[str, Any]:
    """Sondea el grupo y reduce sus parciales en cuanto todos tuvieron éxito.

    Distingue éxito/fallo por tarea en vez de solo `ready()` (un grupo listo
    puede contener fallos); levanta `TareaFallida` ante el primer fallo
    definitivo detectado, sin esperar `completed_count() == total`. Cada
    `AsyncResult` aporta su parcial una sola vez.
    """
    if not grupo.results:
        return {"contador": {}, "lineas": 0, "invalidas": 0, "workers": 0}

    deadline = None if timeout is None else time.monotonic() + timeout
    pendientes = list(grupo.results)
    parciales: list[dict[str, Any]] = []

    while pendientes:
        for resultado in list(pendientes):
            if not resultado.ready():
                continue
            if resultado.failed():
                raise TareaFallida(
                    f"Tarea {resultado.id} terminó con fallo definitivo: {resultado.result!r}"
                )
            parciales.append(resultado.result)
            pendientes.remove(resultado)
        if pendientes:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Venció el timeout esperando {len(pendientes)} de "
                    f"{len(grupo.results)} tareas"
                )
            time.sleep(intervalo)

    contador_total = reducir(parcial["contador"] for parcial in parciales)
    workers_observados = {
        (parcial["worker"]["hostname"], parcial["worker"]["pid"]) for parcial in parciales
    }
    return {
        "contador": dict(contador_total),
        "lineas": sum(parcial["lineas"] for parcial in parciales),
        "invalidas": sum(parcial["invalidas"] for parcial in parciales),
        "workers": len(workers_observados),
    }


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.analisis es una biblioteca; no define un punto de entrada propio."
    )

"""Pruebas de `Metricas`: contadores compartidos entre procesos (issue #21).

PROJECT_STATEMENT §11, ADR-0009. Lo que hay que demostrar no es que `+=`
funcione en un solo proceso (eso no prueba nada sobre IPC), sino que el lock
evita incrementos perdidos cuando varios procesos escriben la misma métrica
a la vez (invariante 4 de CLAUDE.md).
"""

from __future__ import annotations

import multiprocessing

from loghunter import ipc


def test_snapshot_refleja_cada_contador_de_forma_independiente() -> None:
    metricas = ipc.Metricas()
    metricas.incrementar_jobs_activos()
    metricas.incrementar_jobs_activos()
    metricas.decrementar_jobs_activos()
    metricas.incrementar_informes_emitidos()
    metricas.incrementar_informes_persistidos()
    metricas.sumar_lineas_validas(300)
    metricas.sumar_lineas_validas(50)

    assert metricas.snapshot() == {
        "jobs_activos": 1,
        "informes_emitidos": 1,
        "informes_persistidos": 1,
        "lineas_validas": 350,
    }


def _incrementar_n_veces(metricas: ipc.Metricas, veces: int) -> None:
    for _ in range(veces):
        metricas.incrementar_informes_persistidos()


def test_incrementos_concurrentes_entre_procesos_no_se_pierden() -> None:
    """Sin el lock de `get_lock()`, un read-modify-write concurrente perdería
    incrementos; con varios procesos reales escribiendo la misma métrica el
    total debe ser exacto."""
    metricas = ipc.Metricas()
    procesos = [10] * 8  # un valor suficientemente grande para exponer carreras
    veces = procesos[0]

    workers = [
        multiprocessing.Process(target=_incrementar_n_veces, args=(metricas, veces))
        for _ in procesos
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    assert metricas.snapshot()["informes_persistidos"] == len(procesos) * veces


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

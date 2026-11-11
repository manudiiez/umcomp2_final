"""Contadores compartidos entre coordinador e indexador (issue #21).

`multiprocessing.Value` con `lock=True` expone un `Lock` propio vía
`get_lock()`, pero **no** protege automáticamente `+=` ni la lectura de
`.value`: eso exige tomar el lock explícitamente alrededor de cada acceso
(invariante 4 de CLAUDE.md, ADR-0009). `Metricas` agrupa los cuatro
contadores escalares de PROJECT_STATEMENT §11 y expone un método por evento,
cada uno con su propio responsable (coordinador o indexador) y momento
exactos de esa tabla; nunca se expone `.value` crudo para evitar un acceso
sin lock desde otro módulo.
"""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass, field
from typing import Any


def _contador() -> Any:
    """`Value` con signo: ninguna de estas métricas puede decrecer de cero
    salvo `jobs_activos`, que sí baja al liberarse un cupo."""
    return multiprocessing.Value("q", 0)


@dataclass(slots=True)
class Metricas:
    """Cuatro contadores compartidos, uno por fila de PROJECT_STATEMENT §11.

    - `jobs_activos`: coordinador, admisión y liberación de cupo.
    - `informes_emitidos`: coordinador, tras enviar una respuesta terminal exitosa.
    - `informes_persistidos`: indexador, tras confirmar una transacción.
    - `lineas_validas`: coordinador, una vez por job tras un reduce exitoso.
    """

    jobs_activos: Any = field(default_factory=_contador)
    informes_emitidos: Any = field(default_factory=_contador)
    informes_persistidos: Any = field(default_factory=_contador)
    lineas_validas: Any = field(default_factory=_contador)

    def incrementar_jobs_activos(self) -> None:
        with self.jobs_activos.get_lock():
            self.jobs_activos.value += 1

    def decrementar_jobs_activos(self) -> None:
        with self.jobs_activos.get_lock():
            self.jobs_activos.value -= 1

    def incrementar_informes_emitidos(self) -> None:
        with self.informes_emitidos.get_lock():
            self.informes_emitidos.value += 1

    def incrementar_informes_persistidos(self) -> None:
        with self.informes_persistidos.get_lock():
            self.informes_persistidos.value += 1

    def sumar_lineas_validas(self, cantidad: int) -> None:
        with self.lineas_validas.get_lock():
            self.lineas_validas.value += cantidad

    def snapshot(self) -> dict[str, int]:
        """Lectura consistente de los cuatro contadores, cada uno bajo su lock."""
        return {
            "jobs_activos": _leer(self.jobs_activos),
            "informes_emitidos": _leer(self.informes_emitidos),
            "informes_persistidos": _leer(self.informes_persistidos),
            "lineas_validas": _leer(self.lineas_validas),
        }


def _leer(valor: Any) -> int:
    with valor.get_lock():
        return valor.value


if __name__ == "__main__":
    raise SystemExit("loghunter.ipc es una biblioteca; no define un punto de entrada propio.")

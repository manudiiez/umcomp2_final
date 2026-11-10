"""Pruebas del indexador: esquema, atomicidad y consumo de la cola (issue #20).

PROJECT_STATEMENT §11, ADR-0008. `conectar`/`guardar_informe` se prueban
directamente con SQLite real (no hay nada que simular: es la única biblioteca
de persistencia). `ejecutar` se prueba como lo que es, un proceso hijo real
con `multiprocessing.Process` y `Queue`, para demostrar ese IPC end-to-end
sin acoplar esta prueba al coordinador. `metricas` se recibe por duck typing
(solo necesita `incrementar_informes_persistidos()`); los contadores reales
con `Value`/`Lock` son el issue #21, así que aquí se usa un contador simple
de proceso, pasado al hijo igual que cualquier objeto picklable.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from loghunter import indexador


@dataclass
class _MetricasFalsas:
    """Cuenta persistencias en memoria compartida simple, sin `Value`/`Lock`.

    Alcanza para esta prueba porque un solo proceso hijo incrementa el
    contador de manera secuencial; no demuestra la sincronización entre
    procesos, que es exactamente lo que agrega `ipc.Metricas` (issue #21).
    """

    contador: Any = field(default_factory=lambda: multiprocessing.Value("q", 0))

    def incrementar_informes_persistidos(self) -> None:
        with self.contador.get_lock():
            self.contador.value += 1

    @property
    def informes_persistidos(self) -> int:
        with self.contador.get_lock():
            return self.contador.value


def _informe(job_id: str = "job-1", **extra: Any) -> dict[str, Any]:
    base = {
        "job_id": job_id,
        "ts": 1_700_000_000.0,
        "archivo": "local:///a.log",
        "tamano_bytes": 1024,
        "query": "top-ips",
        "top": 1,
        "chunk_mb": 32,
        "chunks": 2,
        "workers": 1,
        "lineas": 10,
        "invalidas": 0,
        "duracion": 0.5,
        "resultados": [
            {"clave": "10.0.0.1", "valor": 7},
            {"clave": "10.0.0.2", "valor": 3},
        ],
    }
    base.update(extra)
    return base


def test_conectar_crea_el_esquema_y_habilita_claves_foraneas(tmp_path: Path) -> None:
    conn = indexador.conectar(str(tmp_path / "informes.sqlite"))
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tablas = {
            fila[0]
            for fila in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"informes", "resultados"} <= tablas
    finally:
        conn.close()


def test_guardar_informe_persiste_metadatos_y_agregado_completo(tmp_path: Path) -> None:
    conn = indexador.conectar(str(tmp_path / "informes.sqlite"))
    try:
        assert indexador.guardar_informe(conn, _informe(top=1)) is True

        fila = conn.execute(
            "SELECT job_id, top, chunk_mb, lineas FROM informes"
        ).fetchone()
        assert fila == ("job-1", 1, 32, 10)

        # El histórico conserva TODAS las claves, aunque `top` haya sido 1
        # (CU-16: el recorte es solo para la vista del cliente).
        resultados = conn.execute(
            "SELECT clave, valor FROM resultados ORDER BY clave"
        ).fetchall()
        assert resultados == [("10.0.0.1", 7), ("10.0.0.2", 3)]
    finally:
        conn.close()


def test_guardar_informe_entrega_repetida_no_duplica_filas(tmp_path: Path) -> None:
    conn = indexador.conectar(str(tmp_path / "informes.sqlite"))
    try:
        assert indexador.guardar_informe(conn, _informe()) is True
        assert indexador.guardar_informe(conn, _informe()) is False

        total = conn.execute("SELECT COUNT(*) FROM informes").fetchone()[0]
        assert total == 1
    finally:
        conn.close()


def test_guardar_informe_error_de_integridad_revierte_toda_la_transaccion(
    tmp_path: Path,
) -> None:
    conn = indexador.conectar(str(tmp_path / "informes.sqlite"))
    try:
        informe_roto = _informe(
            job_id="job-roto",
            resultados=[
                {"clave": "dup", "valor": 1},
                {"clave": "dup", "valor": 2},  # viola PRIMARY KEY (informe_id, clave)
            ],
        )
        with pytest.raises(sqlite3.IntegrityError):
            indexador.guardar_informe(conn, informe_roto)

        # Ni el informe ni sus resultados quedaron confirmados.
        assert conn.execute("SELECT COUNT(*) FROM informes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM resultados").fetchone()[0] == 0
    finally:
        conn.close()


def test_ejecutar_consume_la_cola_hasta_el_centinela_y_persiste_en_un_proceso_hijo(
    tmp_path: Path,
) -> None:
    ruta_db = str(tmp_path / "informes.sqlite")
    cola: multiprocessing.Queue = multiprocessing.Queue()
    metricas = _MetricasFalsas()

    cola.put(_informe(job_id="job-a"))
    cola.put(_informe(job_id="job-a"))  # repetido: no debe contarse dos veces
    cola.put(_informe(job_id="job-b"))
    cola.put(None)  # centinela

    proceso = multiprocessing.Process(target=indexador.ejecutar, args=(cola, ruta_db, metricas))
    proceso.start()
    proceso.join(timeout=10)
    assert not proceso.is_alive()
    assert proceso.exitcode == 0

    conn = sqlite3.connect(ruta_db)
    try:
        ids = {fila[0] for fila in conn.execute("SELECT job_id FROM informes").fetchall()}
    finally:
        conn.close()
    assert ids == {"job-a", "job-b"}
    assert metricas.informes_persistidos == 2


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

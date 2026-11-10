"""Proceso hijo: único escritor de SQLite, histórico de informes (issue #20).

`conectar`/`guardar_informe` son la biblioteca usada por `ejecutar`, el
*target* real del proceso hijo que el coordinador lanza (issue #21). Una
transacción por informe: metadatos y agregado completo, o nada
(PROJECT_STATEMENT §11, ADR-0008). `job_id UNIQUE` hace que una entrega
repetida no duplique filas; se detecta con un `SELECT` previo dentro de la
misma transacción, no capturando `IntegrityError`, para no confundir ese caso
esperado con un error real de otra restricción (p. ej. una clave repetida en
`resultados`), que sí debe revertir, registrarse y no contar como persistido.

El hijo ignora `SIGINT` (lo organiza el padre, CU-19) y, si recibe `SIGTERM`
directamente, interrumpe el `cola.get()` bloqueante para cerrar su conexión
de forma controlada en vez de terminar a mitad de una lectura.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
from typing import Any, Mapping

logger = logging.getLogger(__name__)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS informes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    ts REAL NOT NULL,
    archivo TEXT NOT NULL,
    tamano_bytes INTEGER NOT NULL,
    query TEXT NOT NULL,
    top INTEGER NOT NULL,
    chunk_mb INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    workers INTEGER,
    lineas INTEGER NOT NULL,
    invalidas INTEGER NOT NULL,
    duracion REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS resultados (
    informe_id INTEGER NOT NULL REFERENCES informes(id),
    clave TEXT NOT NULL,
    valor INTEGER NOT NULL,
    PRIMARY KEY (informe_id, clave)
);
"""


class _CierrePorSenal(Exception):
    """Señala que `SIGTERM` interrumpió la espera bloqueante de `ejecutar`."""


def conectar(ruta_db: str) -> sqlite3.Connection:
    """Abre la conexión propia del indexador: esquema normativo y FK habilitadas."""
    conn = sqlite3.connect(ruta_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(ESQUEMA)
    return conn


def guardar_informe(conn: sqlite3.Connection, informe: Mapping[str, Any]) -> bool:
    """Guarda metadatos y agregado completo en una transacción; `True` si es nuevo.

    Una entrega repetida del mismo `job_id` no duplica filas: se detecta antes
    de escribir y se devuelve `False` sin tocar la base. Cualquier otro error
    (por ejemplo una clave repetida en `resultados`) propaga la excepción tras
    revertir toda la transacción vía el *context manager* de `conn`.
    """
    with conn:
        ya_existe = conn.execute(
            "SELECT 1 FROM informes WHERE job_id = ?", (informe["job_id"],)
        ).fetchone()
        if ya_existe is not None:
            return False

        cursor = conn.execute(
            """
            INSERT INTO informes
                (job_id, ts, archivo, tamano_bytes, query, top, chunk_mb,
                 chunks, workers, lineas, invalidas, duracion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                informe["job_id"],
                informe["ts"],
                informe["archivo"],
                informe["tamano_bytes"],
                informe["query"],
                informe["top"],
                informe["chunk_mb"],
                informe["chunks"],
                informe["workers"],
                informe["lineas"],
                informe["invalidas"],
                informe["duracion"],
            ),
        )
        informe_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO resultados (informe_id, clave, valor) VALUES (?, ?, ?)",
            [(informe_id, item["clave"], item["valor"]) for item in informe["resultados"]],
        )
    return True


def _ignorar_sigint(signum: int, frame: Any) -> None:
    del signum, frame


def _manejar_sigterm(signum: int, frame: Any) -> None:
    del signum, frame
    raise _CierrePorSenal()


def ejecutar(cola: Any, ruta_db: str, metricas: Any) -> None:
    """*Target* del proceso hijo (issue #21): consume `cola` hasta el centinela.

    `cola.get()` devuelve un informe completo o `None` (centinela de cierre,
    CU-19). Cada informe se guarda en su propia transacción; un fallo se
    registra y no incrementa `informes_persistidos` (ese contador solo avanza
    tras un commit real, PROJECT_STATEMENT §11).
    """
    signal.signal(signal.SIGINT, _ignorar_sigint)
    signal.signal(signal.SIGTERM, _manejar_sigterm)

    conn = conectar(ruta_db)
    try:
        while True:
            try:
                informe = cola.get()
            except _CierrePorSenal:
                logger.info("SIGTERM recibido: cierro la conexión SQLite de forma controlada")
                break
            if informe is None:
                break
            try:
                nuevo = guardar_informe(conn, informe)
            except Exception:
                logger.exception(
                    "Fallo guardando el informe job_id=%s; transacción revertida",
                    informe.get("job_id"),
                )
                continue
            if nuevo:
                metricas.incrementar_informes_persistidos()
            else:
                logger.info(
                    "job_id=%s ya estaba persistido: entrega repetida ignorada",
                    informe["job_id"],
                )
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.indexador no se ejecuta como script: el coordinador lo lanza "
        "como proceso hijo con multiprocessing.Process (issue #21)."
    )

"""Núcleo del map-reduce: rangos y procesamiento de un tramo (issue #7).

Separa dos responsabilidades que la tarea Celery del issue #10 reutilizará
sin cambios:

- `calcular_rangos`: aritmética pura sobre el tamaño del archivo, sin leer
  contenido (PROJECT_STATEMENT §8.1). La usa el coordinador para planificar.
- `map_chunk`: alinea un tramo `[inicio, fin)` a fin de línea y devuelve el
  agregado parcial (PROJECT_STATEMENT §8.2). Es puro e idempotente: solo
  depende de sus argumentos, nunca muta estado externo.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

from loghunter import lector as lector_mod
from loghunter.consultas import agregar_parcial
from loghunter.lector import LectorLocal
from loghunter.parser import parsear_linea

_BLOQUE = 64 * 1024  # bytes de lectura auxiliar; no es una cota de longitud de línea.


def calcular_rangos(tamano: int, chunk_bytes: int) -> list[tuple[int, int]]:
    """Divide `[0, tamano)` en tramos semiabiertos de hasta `chunk_bytes`.

    Un archivo vacío (`tamano <= 0`) no genera tramos. La cantidad es
    `ceil(tamano / chunk_bytes)`.
    """
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes debe ser positivo")
    if tamano <= 0:
        return []
    cantidad = math.ceil(tamano / chunk_bytes)
    return [
        (i * chunk_bytes, min(i * chunk_bytes + chunk_bytes, tamano))
        for i in range(cantidad)
    ]


def map_chunk(uri: str, inicio: int, fin: int, query: str) -> dict[str, Any]:
    """Procesa el tramo `[inicio, fin)` de `uri` y devuelve el agregado parcial.

    Idempotente: el resultado depende solo de `(uri, inicio, fin, query)`.
    Cada línea se cuenta en el único tramo que contiene el offset de su
    primer byte, aunque su contenido se extienda más allá de `fin`.
    """
    lector = lector_mod.abrir(uri)
    try:
        validas = 0
        invalidas = 0
        registros = []
        for linea in _lineas_del_tramo(lector, inicio, fin):
            registro = parsear_linea(linea)
            if registro is None:
                invalidas += 1
            else:
                validas += 1
                registros.append(registro)
    finally:
        lector.cerrar()

    contador = agregar_parcial(query, registros)
    return {"contador": dict(contador), "validas": validas, "invalidas": invalidas}


def _lineas_del_tramo(lector: LectorLocal, inicio: int, fin: int) -> Iterator[bytes]:
    """Genera las líneas cuyo primer byte cae en `[inicio, fin)`, completas."""
    cursor = _inicio_alineado(lector, inicio)
    while cursor < fin:
        linea = _leer_una_linea(lector, cursor)
        if not linea:
            return
        cursor += len(linea)
        yield linea


def _inicio_alineado(lector: LectorLocal, inicio: int) -> int:
    """Primer offset de inicio de línea en o después de `inicio`.

    Si `inicio == 0` ya es inicio. Si no, se consulta el byte anterior: si es
    LF, `inicio` ya está alineado; si no, se descarta la cola de la línea
    anterior avanzando hasta el siguiente LF o EOF.
    """
    if inicio == 0:
        return 0
    anterior = lector.leer(inicio - 1, inicio)
    if anterior == b"\n":
        return inicio

    cursor = inicio
    while True:
        bloque = lector.leer_hasta_salto(cursor, _BLOQUE)
        if not bloque:
            return lector.tamano()
        cursor += len(bloque)
        if bloque.endswith(b"\n"):
            return cursor


def _leer_una_linea(lector: LectorLocal, desde: int) -> bytes:
    """Lee una línea completa desde `desde` hasta LF o EOF, en bloques de 64 KiB."""
    partes = []
    cursor = desde
    while True:
        bloque = lector.leer_hasta_salto(cursor, _BLOQUE)
        if not bloque:
            break
        partes.append(bloque)
        cursor += len(bloque)
        if bloque.endswith(b"\n"):
            break
    return b"".join(partes)


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.workers es una biblioteca; no define un punto de entrada propio."
    )

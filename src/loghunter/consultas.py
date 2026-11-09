"""Agregadores combinables de las cuatro consultas y referencia secuencial (issue #4).

Las cuatro consultas (`top-ips`, `status`, `urls`, `traffic`) producen contadores
que se pueden sumar entre tramos sin perder exactitud: cada parcial es un
``Counter`` por clave, y el reduce es una suma de contadores. El top se aplica
siempre después de reducir, nunca dentro de un tramo (PROJECT_STATEMENT §7.2).

``calcular_referencia`` procesa el archivo completo en un solo proceso, sin
fragmentar en tramos; sirve como oráculo para comparar contra el resultado
distribuido/por tramos (issue #8), no como implementación de producción.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable
from datetime import timezone

from loghunter.parser import Registro, parsear_linea

CONSULTAS = ("top-ips", "status", "urls", "traffic")


def _clave_valor(query: str, registro: Registro) -> tuple[str, int]:
    """Clave y valor a acumular para un registro, según la consulta."""
    if query == "top-ips":
        return registro.ip, 1
    if query == "status":
        return str(registro.estado), 1
    if query == "urls":
        return registro.destino, 1
    if query == "traffic":
        hora_utc = registro.timestamp.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        return hora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), registro.bytes
    raise ValueError(f"Consulta desconocida: {query}")


def agregar_parcial(query: str, registros: Iterable[Registro]) -> Counter[str]:
    """Contador parcial de un tramo para la consulta dada; combinable con otros."""
    if query not in CONSULTAS:
        raise ValueError(f"Consulta desconocida: {query}")
    contador: Counter[str] = Counter()
    for registro in registros:
        clave, valor = _clave_valor(query, registro)
        contador[clave] += valor
    return contador


def reducir(parciales: Iterable[Counter[str]]) -> Counter[str]:
    """Suma contadores parciales de un mismo job en un total global."""
    total: Counter[str] = Counter()
    for parcial in parciales:
        total.update(parcial)
    return total


def presentar(
    query: str, total: Counter[str], top: int | None = None
) -> list[tuple[str, int]]:
    """Ordena el total según la consulta; `--top` solo recorta `top-ips`/`urls`."""
    if query not in CONSULTAS:
        raise ValueError(f"Consulta desconocida: {query}")
    if query in ("top-ips", "urls"):
        items = sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))
        return items[:top] if top is not None else items
    if query == "status":
        return sorted(total.items(), key=lambda kv: int(kv[0]))
    return sorted(total.items(), key=lambda kv: kv[0])  # traffic: orden cronológico


def calcular_referencia(ruta: Path, query: str) -> tuple[Counter[str], int, int]:
    """Procesa el archivo completo en un solo proceso, sin dividir en tramos.

    Referencia de desarrollo para comparar contra el resultado por tramos de
    los workers (issue #7/#8); no fragmenta, no usa `lector.py`.
    """
    validas = 0
    invalidas = 0
    contador: Counter[str] = Counter()
    with open(ruta, "rb") as archivo:
        for linea in archivo:
            registro = parsear_linea(linea)
            if registro is None:
                invalidas += 1
                continue
            validas += 1
            clave, valor = _clave_valor(query, registro)
            contador[clave] += valor
    return contador, validas, invalidas


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.consultas es una biblioteca; no define un punto de entrada propio."
    )

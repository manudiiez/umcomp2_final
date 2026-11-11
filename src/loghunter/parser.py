"""Parser del formato combined de Apache/Nginx (issue #3).

Interpreta y valida una línea completa sin depender de cómo se leyó ni de
cómo se transportará. Un registro inválido (codificación, estructura o campo
fuera de rango) no aborta nada: quien llama decide cómo contarlo.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_MESES = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

_CAMPO_CITADO = r'"(?:[^"\\]|\\.)*"'

_PATRON_REGISTRO = re.compile(
    r"^(?P<ip>\S+) \S+ \S+ "
    r"\[(?P<dia>\d{2})/(?P<mes>[A-Za-z]{3})/(?P<anio>\d{4}):"
    r"(?P<hora>\d{2}):(?P<minuto>\d{2}):(?P<segundo>\d{2}) "
    r"(?P<signo>[+-])(?P<offh>\d{2})(?P<offm>\d{2})\] "
    r'"(?P<peticion>(?:[^"\\]|\\.)*)" '
    r"(?P<estado>\d{3}) (?P<bytes>-|\d+) "
    rf"{_CAMPO_CITADO} {_CAMPO_CITADO}$"
)

_PATRON_PETICION = re.compile(r"^(?P<metodo>[A-Za-z]+) (?P<destino>\S+) HTTP/\d\.\d$")

_PATRON_ESCAPE = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class Registro:
    """Un registro combined ya validado, listo para los agregadores."""

    ip: str
    timestamp: datetime
    metodo: str
    destino: str
    estado: int
    bytes: int


def parsear_linea(linea: bytes) -> Registro | None:
    """Interpreta una línea combined; devuelve ``None`` si es inválida.

    Acepta el salto final en LF o CRLF, o su ausencia (última línea del
    archivo). Una línea vacía es inválida.
    """
    linea = linea.rstrip(b"\r\n")
    if not linea:
        return None

    try:
        texto = linea.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

    coincidencia = _PATRON_REGISTRO.match(texto)
    if coincidencia is None:
        return None
    campos = coincidencia.groupdict()

    try:
        ip = ipaddress.ip_address(campos["ip"])
    except ValueError:
        return None

    mes = _MESES.get(campos["mes"])
    if mes is None:
        return None

    signo = 1 if campos["signo"] == "+" else -1
    offset = signo * timedelta(hours=int(campos["offh"]), minutes=int(campos["offm"]))
    try:
        timestamp = datetime(
            int(campos["anio"]),
            mes,
            int(campos["dia"]),
            int(campos["hora"]),
            int(campos["minuto"]),
            int(campos["segundo"]),
            tzinfo=timezone(offset),
        )
    except ValueError:
        return None

    peticion = _PATRON_PETICION.match(_desescapar(campos["peticion"]))
    if peticion is None:
        return None

    estado = int(campos["estado"])
    if not (100 <= estado <= 599):
        return None

    bytes_ = 0 if campos["bytes"] == "-" else int(campos["bytes"])

    return Registro(
        ip=str(ip),
        timestamp=timestamp,
        metodo=peticion["metodo"],
        destino=peticion["destino"],
        estado=estado,
        bytes=bytes_,
    )


def _desescapar(campo: str) -> str:
    """Revierte los escapes ``\\"`` y ``\\\\`` de un campo citado combined."""
    return _PATRON_ESCAPE.sub(r"\1", campo)


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.parser es una biblioteca; no define un punto de entrada propio."
    )

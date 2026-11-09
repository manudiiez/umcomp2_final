"""Acceso por rangos a archivos locales y objetos S3 (issues #6 y #11).

Esta etapa implementa solo el backend `local://` (`mmap` de solo lectura). El
backend `s3://` (`GET Range` contra MinIO) se agrega en el issue #11. El
contrato es el mismo para ambos: `tamano()`, `leer(inicio, fin)` con rango
semiabierto y `leer_hasta_salto(desde, max_bytes)`. Este módulo no interpreta
registros: solo entrega bytes.
"""

from __future__ import annotations

import mmap
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def normalizar_uri_local(uri: str) -> str:
    """Normaliza una ruta o `local://` a `local:///ruta/absoluta`."""
    if uri.startswith("local://"):
        ruta = unquote(urlsplit(uri).path)
    else:
        ruta = uri
    absoluta = str(Path(ruta).expanduser().resolve())
    return f"local://{absoluta}"


class LectorLocal:
    """Lector de rangos de bytes de un archivo local vía `mmap` de solo lectura."""

    def __init__(self, uri: str) -> None:
        self.uri = normalizar_uri_local(uri)
        self._ruta = Path(self.uri[len("local://") :])
        if not self._ruta.is_file():
            raise FileNotFoundError(f"No existe el archivo local: {self._ruta}")
        self._fd = os.open(self._ruta, os.O_RDONLY)
        self._tamano = os.fstat(self._fd).st_size
        self._cerrado = False

    def tamano(self) -> int:
        """Tamaño del archivo en bytes, obtenido una sola vez al abrir."""
        return self._tamano

    def leer(self, inicio: int, fin: int) -> bytes:
        """Devuelve exactamente los bytes `[inicio, fin)`; `b""` si el rango está vacío."""
        self._validar_rango(inicio, fin)
        if inicio == fin:
            return b""

        granularidad = mmap.ALLOCATIONGRANULARITY
        offset_alineado = (inicio // granularidad) * granularidad
        desplazamiento = inicio - offset_alineado
        longitud = fin - offset_alineado

        with mmap.mmap(
            self._fd, length=longitud, access=mmap.ACCESS_READ, offset=offset_alineado
        ) as mapa:
            return bytes(mapa[desplazamiento : desplazamiento + (fin - inicio)])

    def leer_hasta_salto(self, desde: int, max_bytes: int) -> bytes:
        """Lee hasta el primer LF inclusive, o hasta `max_bytes`/EOF si no aparece."""
        fin = min(desde + max_bytes, self._tamano)
        datos = self.leer(desde, fin)
        indice = datos.find(b"\n")
        if indice == -1:
            return datos
        return datos[: indice + 1]

    def cerrar(self) -> None:
        """Libera el descriptor del archivo. Idempotente."""
        if not self._cerrado:
            os.close(self._fd)
            self._cerrado = True

    def _validar_rango(self, inicio: int, fin: int) -> None:
        if not (0 <= inicio <= fin <= self._tamano):
            raise ValueError(
                f"Rango fuera del archivo: [{inicio}, {fin}) con tamaño {self._tamano}"
            )

    def __enter__(self) -> "LectorLocal":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cerrar()


def abrir(uri: str) -> LectorLocal:
    """Abre un lector por rangos según el esquema de la URI (solo `local://` por ahora)."""
    esquema = urlsplit(uri).scheme
    if esquema in ("", "local"):
        return LectorLocal(uri)
    raise ValueError(f"Backend no soportado todavía: {esquema}:// (ver issue #11)")


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.lector es una biblioteca; no define un punto de entrada propio."
    )

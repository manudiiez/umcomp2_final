"""Acceso por rangos a archivos locales y objetos S3 (issues #6 y #11).

Dos backends con el mismo contrato: `tamano()`, `leer(inicio, fin)` con rango
semiabierto y `leer_hasta_salto(desde, max_bytes)`. `local://` usa `mmap` de
solo lectura; `s3://` usa `HEAD`/`GET Range` contra MinIO vía `boto3`
(ADR-0004). Este módulo no interpreta registros: solo entrega bytes.
"""

from __future__ import annotations

import mmap
import os
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

_S3_CONNECT_TIMEOUT = 5
_S3_READ_TIMEOUT = 15
_S3_MAX_REINTENTOS = 3
_S3_BACKOFF_BASE = 0.2  # segundos; crece 0.2, 0.4, 0.8
_S3_LECTURA_ADELANTADA = 512 * 1024  # bytes por GET cacheados en leer_hasta_salto (issue #53)


class AlmacenamientoNoDisponible(Exception):
    """Fallo transitorio de almacenamiento remoto tras agotar los reintentos finitos."""


def normalizar_uri_local(uri: str) -> str:
    """Normaliza una ruta o `local://` a `local:///ruta/absoluta`."""
    if uri.startswith("local://"):
        ruta = unquote(urlsplit(uri).path)
    else:
        ruta = uri
    absoluta = str(Path(ruta).expanduser().resolve())
    return f"local://{absoluta}"


def _leer_hasta_salto_generico(lector: "LectorLocal | LectorS3", desde: int, max_bytes: int) -> bytes:
    """Lógica común de `leer_hasta_salto`: hasta el primer LF inclusive, o límite/EOF."""
    fin = min(desde + max_bytes, lector.tamano())
    datos = lector.leer(desde, fin)
    indice = datos.find(b"\n")
    if indice == -1:
        return datos
    return datos[: indice + 1]


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
        return _leer_hasta_salto_generico(self, desde, max_bytes)

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


def normalizar_uri_s3(uri: str) -> tuple[str, str]:
    """Separa una URI `s3://bucket/key` en `(bucket, key)`."""
    partes = urlsplit(uri)
    bucket = partes.netloc
    key = unquote(partes.path).lstrip("/")
    if not bucket or not key:
        raise ValueError(f"URI S3 inválida, se espera s3://bucket/key: {uri}")
    return bucket, key


def cliente_s3() -> "boto3.client":
    endpoint = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    config = Config(
        s3={"addressing_style": "path"},
        connect_timeout=_S3_CONNECT_TIMEOUT,
        read_timeout=_S3_READ_TIMEOUT,
        retries={"max_attempts": 0},
    )
    return boto3.client("s3", endpoint_url=endpoint, region_name=region, config=config)


def _codigo_error(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _es_transitorio(error: ClientError) -> bool:
    """Distingue fallos transitorios (reintentables) de permanentes."""
    if _codigo_error(error) in ("NoSuchKey", "NoSuchBucket", "AccessDenied", "403", "404"):
        return False
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    return status >= 500 or _codigo_error(error) in (
        "SlowDown",
        "RequestTimeout",
        "InternalError",
        "ServiceUnavailable",
    )


class LectorS3:
    """Lector de rangos de bytes de un objeto S3 (MinIO) vía `HEAD`/`GET Range`."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        self._bucket, self._key = normalizar_uri_s3(uri)
        self._cliente = cliente_s3()
        self._cerrado = False
        self._tamano = self._operacion_con_reintentos(
            lambda: self._cliente.head_object(Bucket=self._bucket, Key=self._key)
        )["ContentLength"]
        self._cache_inicio = 0
        self._cache_datos = b""

    def tamano(self) -> int:
        """Tamaño del objeto en bytes, obtenido una sola vez al abrir (`HEAD`)."""
        return self._tamano

    def leer(self, inicio: int, fin: int) -> bytes:
        """Devuelve exactamente los bytes `[inicio, fin)` vía `GET Range` HTTP inclusivo."""
        self._validar_rango(inicio, fin)
        if inicio == fin:
            return b""

        respuesta = self._operacion_con_reintentos(
            lambda: self._cliente.get_object(
                Bucket=self._bucket, Key=self._key, Range=f"bytes={inicio}-{fin - 1}"
            )
        )
        cuerpo = respuesta["Body"]
        try:
            datos = cuerpo.read()
        finally:
            cuerpo.close()
        if len(datos) != fin - inicio:
            raise AlmacenamientoNoDisponible(
                f"Respuesta incompleta para [{inicio}, {fin}) de {self.uri}: "
                f"se esperaban {fin - inicio} bytes y llegaron {len(datos)}"
            )
        return datos

    def leer_hasta_salto(self, desde: int, max_bytes: int) -> bytes:
        """Lee hasta el primer LF inclusive, o hasta `max_bytes`/EOF si no aparece.

        A diferencia de `LectorLocal` (`mmap`: relecturas superpuestas son
        gratis vía el page cache del kernel), acá cada lectura nueva es un
        `GET Range` HTTP real. `_lineas_del_tramo` llama a este método una
        vez por línea con un cursor que avanza por el tramo; sin cachear,
        un tramo de muchas líneas cortas dispararía una petición nueva por
        línea, descartando casi todo lo traído (issue #53). Se cachea el
        último bloque de `_S3_LECTURA_ADELANTADA` bytes y se reutiliza
        mientras el cursor siga dentro de él.
        """
        limite_absoluto = min(desde + max_bytes, self._tamano)
        ventana = limite_absoluto - desde
        if ventana <= 0:
            return b""

        cache_cubre = (
            self._cache_inicio <= desde
            and desde + ventana <= self._cache_inicio + len(self._cache_datos)
        )
        if not cache_cubre:
            objetivo = min(desde + max(max_bytes, _S3_LECTURA_ADELANTADA), self._tamano)
            self._cache_datos = self.leer(desde, objetivo)
            self._cache_inicio = desde

        offset = desde - self._cache_inicio
        disponible = self._cache_datos[offset : offset + ventana]
        indice = disponible.find(b"\n")
        if indice == -1:
            return disponible
        return disponible[: indice + 1]

    def cerrar(self) -> None:
        """Cierra el cliente HTTP subyacente. Idempotente."""
        if not self._cerrado:
            self._cliente.close()
            self._cerrado = True

    def _validar_rango(self, inicio: int, fin: int) -> None:
        if not (0 <= inicio <= fin <= self._tamano):
            raise ValueError(
                f"Rango fuera del objeto: [{inicio}, {fin}) con tamaño {self._tamano}"
            )

    def _operacion_con_reintentos(self, operacion):
        """Reintentos finitos con backoff solo para fallos transitorios (PROJECT_STATEMENT §9.3)."""
        ultimo_error: Exception | None = None
        for intento in range(_S3_MAX_REINTENTOS + 1):
            try:
                return operacion()
            except ClientError as error:
                if not _es_transitorio(error):
                    raise self._traducir_error(error) from error
                ultimo_error = error
            except EndpointConnectionError as error:
                ultimo_error = error
            if intento < _S3_MAX_REINTENTOS:
                time.sleep(_S3_BACKOFF_BASE * (2**intento))
        raise AlmacenamientoNoDisponible(
            f"Fallo transitorio persistente leyendo {self.uri}: {ultimo_error}"
        ) from ultimo_error

    def _traducir_error(self, error: ClientError) -> Exception:
        codigo = _codigo_error(error)
        if codigo in ("NoSuchKey", "NoSuchBucket", "404"):
            return FileNotFoundError(f"No existe el objeto S3: {self.uri}")
        if codigo in ("AccessDenied", "403"):
            return PermissionError(f"Acceso denegado al objeto S3: {self.uri}")
        return AlmacenamientoNoDisponible(f"Fallo S3 ({codigo}) leyendo {self.uri}")

    def __enter__(self) -> "LectorS3":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cerrar()


def abrir(uri: str) -> LectorLocal | LectorS3:
    """Abre un lector por rangos según el esquema de la URI (`local://` o `s3://`)."""
    esquema = urlsplit(uri).scheme
    if esquema in ("", "local"):
        return LectorLocal(uri)
    if esquema == "s3":
        return LectorS3(uri)
    raise ValueError(f"Backend no soportado: {esquema}://")


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.lector es una biblioteca; no define un punto de entrada propio."
    )

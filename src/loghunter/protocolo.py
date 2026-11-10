"""Protocolo de aplicación v1: mensajes JSON delimitados por LF (issue #15).

Framing y validación independientes de `recv`/`send`: TCP puede fragmentar o
juntar mensajes, así que se lee siempre por delimitador. El pedido tiene un
límite de tamaño y de tiempo de lectura propios (PROJECT_STATEMENT §10); el
informe final no comparte ese límite. No se usa `pickle`: JSON UTF-8 por línea,
con `version: 1` y rechazo estricto de tipos, campos y versiones desconocidas.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

VERSION = 1
LIMITE_PEDIDO = 64 * 1024
TIMEOUT_PEDIDO = 30.0

CONSULTAS = ("top-ips", "status", "urls", "traffic")
_ESQUEMAS_SOPORTADOS = ("", "local", "s3")
_CAMPOS_PEDIDO = {"version", "type", "file", "query", "top", "chunk_mb"}
_CAMPOS_OBLIGATORIOS = ("version", "type", "file", "query")

CODIGOS = frozenset(
    {
        "INVALID_REQUEST",
        "UNSUPPORTED_QUERY",
        "UNSUPPORTED_URI",
        "FILE_NOT_FOUND",
        "FILE_ACCESS_ERROR",
        "STORAGE_UNAVAILABLE",
        "BROKER_UNAVAILABLE",
        "TASK_FAILED",
        "JOB_TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "SERVER_SHUTDOWN",
        "INTERNAL_ERROR",
    }
)


class ErrorProtocolo(Exception):
    """Error terminal de un pedido, con su código de PROJECT_STATEMENT §10.2.

    `job_id` es `None` cuando el error precede a la creación del job (todo lo
    estructural: framing, versión, campos, consulta o esquema de URI).
    """

    def __init__(self, code: str, message: str, job_id: str | None = None) -> None:
        if code not in CODIGOS:
            raise ValueError(f"Código de error desconocido: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.job_id = job_id


def _es_entero_estricto(valor: object) -> bool:
    """`bool` es subclase de `int` en Python; el protocolo exige rechazarlo."""
    return isinstance(valor, int) and not isinstance(valor, bool)


def _esquema_uri(uri: str) -> str:
    return urlsplit(uri).scheme


def validar_uri(uri: str) -> None:
    """Valida el esquema de una URI sin acceder al almacenamiento.

    Esquemas fuera de `local://`/`s3://`/ruta sin esquema, o sufijo `.gz`, son
    estructuralmente inválidos (PROJECT_STATEMENT §3.2 y §10.2).
    """
    if uri.endswith(".gz"):
        raise ErrorProtocolo("UNSUPPORTED_URI", "Los logs comprimidos (.gz) no están soportados")
    if _esquema_uri(uri) not in _ESQUEMAS_SOPORTADOS:
        raise ErrorProtocolo("UNSUPPORTED_URI", f"Esquema de URI no soportado: {uri!r}")


def validar_pedido(datos: Any) -> dict[str, Any]:
    """Valida y normaliza un pedido ya decodificado como JSON.

    Devuelve `{version, type, file, query, top, chunk_mb}` con `top=10` por
    default y `chunk_mb=None` si el cliente no lo especificó (el coordinador
    decide ese default). Rechaza objetos ajenos, campos desconocidos, tipos
    incorrectos (`bool` no es un entero válido) y valores fuera de rango.
    """
    if not isinstance(datos, dict):
        raise ErrorProtocolo("INVALID_REQUEST", "El pedido debe ser un objeto JSON")

    desconocidos = set(datos) - _CAMPOS_PEDIDO
    if desconocidos:
        raise ErrorProtocolo(
            "INVALID_REQUEST", f"Campos desconocidos en el pedido: {sorted(desconocidos)}"
        )
    faltantes = [campo for campo in _CAMPOS_OBLIGATORIOS if campo not in datos]
    if faltantes:
        raise ErrorProtocolo(
            "INVALID_REQUEST", f"Faltan campos obligatorios: {sorted(faltantes)}"
        )

    if datos["version"] != VERSION or not _es_entero_estricto(datos["version"]):
        raise ErrorProtocolo(
            "INVALID_REQUEST", f"Versión de protocolo no soportada: {datos['version']!r}"
        )
    if datos["type"] != "analyze":
        raise ErrorProtocolo("INVALID_REQUEST", f"Tipo de pedido no soportado: {datos['type']!r}")

    archivo = datos["file"]
    if not isinstance(archivo, str) or not archivo:
        raise ErrorProtocolo("INVALID_REQUEST", "'file' debe ser una cadena no vacía")

    query = datos["query"]
    if not isinstance(query, str) or query not in CONSULTAS:
        raise ErrorProtocolo("UNSUPPORTED_QUERY", f"Consulta desconocida: {query!r}")

    top = datos.get("top", 10)
    if not _es_entero_estricto(top) or top <= 0:
        raise ErrorProtocolo("INVALID_REQUEST", f"'top' debe ser un entero positivo: {top!r}")

    chunk_mb = datos.get("chunk_mb")
    if chunk_mb is not None and (not _es_entero_estricto(chunk_mb) or not (1 <= chunk_mb <= 256)):
        raise ErrorProtocolo(
            "INVALID_REQUEST", f"'chunk_mb' debe ser un entero entre 1 y 256: {chunk_mb!r}"
        )

    validar_uri(archivo)

    return {
        "version": VERSION,
        "type": "analyze",
        "file": archivo,
        "query": query,
        "top": top,
        "chunk_mb": chunk_mb,
    }


def codificar(mensaje: dict[str, Any]) -> bytes:
    """Serializa un mensaje a JSON UTF-8 terminado en LF."""
    return (json.dumps(mensaje, ensure_ascii=False) + "\n").encode("utf-8")


async def leer_pedido(
    reader: asyncio.StreamReader,
    *,
    limite: int = LIMITE_PEDIDO,
    timeout: float = TIMEOUT_PEDIDO,
) -> dict[str, Any]:
    """Lee y valida un único pedido delimitado por LF desde `reader`.

    El `reader` debe construirse con `limit=LIMITE_PEDIDO` (vía
    `asyncio.start_server`) para que una línea sin LF dentro del límite agote
    el buffer con `LimitOverrunError` en vez de crecer sin cota.
    """
    try:
        linea = await asyncio.wait_for(reader.readline(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise ErrorProtocolo(
            "INVALID_REQUEST", "Se agotó el tiempo de espera del pedido"
        ) from exc
    except (asyncio.LimitOverrunError, ValueError) as exc:
        # `StreamReader.readline()` traduce `LimitOverrunError` a `ValueError`
        # cuando el separador aparece pero la línea ya superó el límite del buffer.
        raise ErrorProtocolo(
            "INVALID_REQUEST", f"El pedido excede el límite de {limite} bytes"
        ) from exc

    if not linea:
        raise ErrorProtocolo(
            "INVALID_REQUEST", "La conexión se cerró antes de recibir un pedido"
        )
    if len(linea) > limite:
        raise ErrorProtocolo("INVALID_REQUEST", f"El pedido excede el límite de {limite} bytes")
    if not linea.endswith(b"\n"):
        raise ErrorProtocolo(
            "INVALID_REQUEST", "El pedido terminó antes de un salto de línea"
        )

    try:
        texto = linea.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ErrorProtocolo("INVALID_REQUEST", "El pedido no es UTF-8 válido") from exc
    try:
        crudo = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ErrorProtocolo("INVALID_REQUEST", "El pedido no es JSON válido") from exc

    return validar_pedido(crudo)


def msg_accepted(job_id: str, state: str) -> dict[str, Any]:
    return {"version": VERSION, "type": "accepted", "job_id": job_id, "state": state}


def msg_started(job_id: str, chunks: int, chunk_mb: int) -> dict[str, Any]:
    return {
        "version": VERSION,
        "type": "started",
        "job_id": job_id,
        "chunks": chunks,
        "chunk_mb": chunk_mb,
    }


def msg_progress(job_id: str, completed: int, total: int) -> dict[str, Any]:
    return {
        "version": VERSION,
        "type": "progress",
        "job_id": job_id,
        "completed": completed,
        "total": total,
    }


def msg_result(job_id: str, **campos: Any) -> dict[str, Any]:
    return {"version": VERSION, "type": "result", "job_id": job_id, **campos}


def msg_error(job_id: str | None, code: str, message: str) -> dict[str, Any]:
    return {"version": VERSION, "type": "error", "job_id": job_id, "code": code, "message": message}


if __name__ == "__main__":
    raise SystemExit(
        "loghunter.protocolo es una biblioteca; no define un punto de entrada propio."
    )

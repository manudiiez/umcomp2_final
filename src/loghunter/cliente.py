"""Cliente CLI: argumentos, conexión TCP y presentación del informe (issue #17).

No hay concurrencia propia que justificar: una conexión bloqueante alcanza
para un pedido por ejecución (PROJECT_STATEMENT §12.1). `socket.create_connection`
resuelve IPv4/IPv6 según la dirección dada. El cliente no lee el log ni usa
Redis/S3 directamente: solo normaliza una ruta local relativa a
`local:///ruta/absoluta` para que el *coordinador* decida si puede acceder a
ella (ADR-0010); eso no sube el archivo.

En modo `--raw`, progreso y diagnósticos van a stderr; stdout recibe
exactamente el objeto JSON terminal (`result` o `error`) seguido de LF, para
poder consumirse con pipes/herramientas (CU-10).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import IO, Any
from urllib.parse import urlsplit

from loghunter import lector, protocolo

_TIMEOUT_CONEXION = 10.0


def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return valor


def _puerto(texto: str) -> int:
    valor = int(texto)
    if not (1 <= valor <= 65535):
        raise argparse.ArgumentTypeError("el puerto debe estar entre 1 y 65535")
    return valor


def _chunk_mb(texto: str) -> int:
    valor = int(texto)
    if not (1 <= valor <= 256):
        raise argparse.ArgumentTypeError("--chunk-mb debe estar entre 1 y 256 MiB")
    return valor


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m loghunter.cliente",
        description="Cliente LogHunter: pide un análisis al coordinador y muestra el informe.",
    )
    parser.add_argument("-i", "--ip", default="127.0.0.1", help="Dirección del coordinador (default: 127.0.0.1)")
    parser.add_argument("-p", "--port", type=_puerto, default=8888, help="Puerto 1-65535 (default: 8888)")
    parser.add_argument("-f", "--file", required=True, help="URI o ruta local del log a analizar")
    parser.add_argument(
        "-q", "--query", required=True, choices=protocolo.CONSULTAS, help="Consulta a ejecutar"
    )
    parser.add_argument("--top", type=_entero_positivo, default=10, help="Entero positivo (default: 10)")
    parser.add_argument(
        "--chunk-mb", type=_chunk_mb, default=None, dest="chunk_mb",
        help="Sugerencia 1-256 MiB; si se omite decide el coordinador",
    )
    parser.add_argument(
        "--raw", action="store_true", help="Escribir un único JSON terminal más LF en stdout"
    )
    return parser


def _normalizar_archivo(ruta: str) -> str:
    """Convierte una ruta local relativa a `local:///ruta/absoluta`; deja intactas las URI con esquema remoto."""
    esquema = urlsplit(ruta).scheme
    if esquema in ("", "local"):
        return lector.normalizar_uri_local(ruta)
    return ruta


def _construir_pedido(args: argparse.Namespace) -> dict[str, Any]:
    pedido: dict[str, Any] = {
        "version": protocolo.VERSION,
        "type": "analyze",
        "file": _normalizar_archivo(args.file),
        "query": args.query,
        "top": args.top,
    }
    if args.chunk_mb is not None:
        pedido["chunk_mb"] = args.chunk_mb
    return pedido


def _mostrar_informe(mensaje: dict[str, Any], salida: IO[str]) -> None:
    print(file=salida)
    print(f"Archivo: {mensaje['file']} ({mensaje['tamano_bytes']} bytes)", file=salida)
    print(
        f"Consulta: {mensaje['query']}  top={mensaje['top']}  chunk_mb={mensaje['chunk_mb']}",
        file=salida,
    )
    print(f"Tramos: {mensaje['chunks']}  workers observados: {mensaje['workers']}", file=salida)
    print(
        f"Líneas válidas: {mensaje['lineas']}  inválidas: {mensaje['invalidas']}  "
        f"total: {mensaje['lineas_totales']}",
        file=salida,
    )
    print(f"Duración: {mensaje['duracion']:.3f} s", file=salida)
    print(file=salida)

    resultados = mensaje["resultados"]
    ancho = max((len(item["clave"]) for item in resultados), default=5)
    for item in resultados:
        print(f"{item['clave']:<{ancho}}  {item['valor']}", file=salida)


def _consumir_respuestas(flujo: IO[bytes], raw: bool, salida_humana: IO[str]) -> int:
    """Lee mensajes del coordinador hasta el terminal y devuelve el código de salida."""
    progreso_mostrado = False

    while True:
        try:
            linea = flujo.readline()
        except OSError as exc:
            print(f"Error leyendo la respuesta del coordinador: {exc}", file=sys.stderr)
            return 1

        if not linea:
            print("El coordinador cerró la conexión sin enviar un resultado", file=sys.stderr)
            return 1
        if not linea.endswith(b"\n"):
            print("El coordinador cerró la conexión a mitad de un mensaje", file=sys.stderr)
            return 1

        try:
            mensaje = json.loads(linea.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("La respuesta del coordinador no es JSON válido", file=sys.stderr)
            return 1

        tipo = mensaje.get("type")

        if tipo == "accepted":
            if mensaje.get("state") == "queued":
                print("Esperando turno: el coordinador está al tope de análisis simultáneos...", file=salida_humana)
            else:
                print("Pedido admitido.", file=salida_humana)
        elif tipo == "started":
            print(
                f"Tramos a procesar: {mensaje['chunks']} (chunk_mb={mensaje['chunk_mb']})",
                file=salida_humana,
            )
        elif tipo == "progress":
            print(
                f"\rProgreso: {mensaje['completed']}/{mensaje['total']}",
                end="",
                file=salida_humana,
                flush=True,
            )
            progreso_mostrado = True
        elif tipo == "result":
            if progreso_mostrado:
                print(file=salida_humana)
            if raw:
                sys.stdout.write(linea.decode("utf-8"))
                sys.stdout.flush()
            else:
                _mostrar_informe(mensaje, salida_humana)
            return 0
        elif tipo == "error":
            if progreso_mostrado:
                print(file=salida_humana)
            if raw:
                sys.stdout.write(linea.decode("utf-8"))
                sys.stdout.flush()
            else:
                print(f"Error [{mensaje.get('code')}]: {mensaje.get('message')}", file=sys.stderr)
            return 1
        else:
            print(f"Mensaje desconocido del coordinador: {mensaje!r}", file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    pedido = _construir_pedido(args)
    salida_humana: IO[str] = sys.stderr if args.raw else sys.stdout

    try:
        sock = socket.create_connection((args.ip, args.port), timeout=_TIMEOUT_CONEXION)
    except OSError as exc:
        print(f"No se pudo conectar a {args.ip}:{args.port}: {exc}", file=sys.stderr)
        return 1
    sock.settimeout(None)  # el análisis puede tardar más que el timeout de conexión

    try:
        try:
            sock.sendall(protocolo.codificar(pedido))
        except OSError as exc:
            print(f"Error enviando el pedido: {exc}", file=sys.stderr)
            return 1

        flujo = sock.makefile("rb")
        return _consumir_respuestas(flujo, args.raw, salida_humana)
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())

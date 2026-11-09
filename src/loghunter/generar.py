"""Generación reproducible de logs combined en disco (issue #5) o MinIO (issue #12).

Genera por bloques, sin acumular el archivo completo en memoria. La misma
semilla y los mismos argumentos producen exactamente los mismos bytes; una
fecha base fija y un PRNG local evitan depender de la hora de ejecución.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from loghunter.parser import parsear_linea

_MESES_INGLES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_TZ_BASE = timezone(timedelta(hours=-3))
_FECHA_BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=_TZ_BASE)
_METODOS = ("GET", "POST", "PUT", "DELETE")
_DESTINOS = ("/", "/api/users", "/api/orders", "/login", "/static/app.js", "/health")
_ESTADOS_PESOS = ((200, 85), (404, 10), (500, 5))
_HORAS_PICO = frozenset({9, 10, 20, 21})
_AGENTE = "loghunter-generar/1.0"


@dataclass(frozen=True, slots=True)
class ResultadoGeneracion:
    """Totales reales producidos; pueden diferir del porcentaje de corrupción pedido."""

    tamano_bytes: int
    validas: int
    invalidas: int

    @property
    def total(self) -> int:
        return self.validas + self.invalidas


def generar_log(
    *,
    size_mb: int,
    ips: int = 5000,
    corrupt_pct: float = 0.0,
    seed: int | None = None,
) -> Iterator[bytes]:
    """Devuelve un generador de líneas combined reproducibles, bloque a bloque.

    Valida los argumentos antes de devolver el generador (no al primer
    ``next()``).
    """
    _validar_argumentos(size_mb, ips, corrupt_pct)
    return _generar_lineas(size_mb=size_mb, ips=ips, corrupt_pct=corrupt_pct, seed=seed)


def _generar_lineas(
    *, size_mb: int, ips: int, corrupt_pct: float, seed: int | None
) -> Iterator[bytes]:
    objetivo = size_mb * (2**20)
    if objetivo == 0:
        return

    rng = random.Random(seed)
    pool_ips = [_ip_aleatoria(rng) for _ in range(ips)]
    instante = _FECHA_BASE
    emitido = 0

    while emitido < objetivo:
        if rng.random() * 100 < corrupt_pct:
            linea = _linea_corrupta(rng)
        else:
            linea = _linea_valida(rng, pool_ips, instante)
        yield linea
        emitido += len(linea)

        paso = rng.randint(1, 5)
        if instante.hour in _HORAS_PICO:
            paso = max(1, paso // 3)
        instante = instante + timedelta(seconds=paso)


def escribir_log(
    *,
    destino: str,
    size_mb: int,
    ips: int = 5000,
    corrupt_pct: float = 0.0,
    seed: int | None = None,
) -> ResultadoGeneracion:
    """Escribe el log generado en `destino` (solo rutas locales en esta etapa)."""
    ruta = _validar_destino_local(destino)
    tamano = 0
    validas = 0
    invalidas = 0
    with open(ruta, "wb") as archivo:
        for linea in generar_log(size_mb=size_mb, ips=ips, corrupt_pct=corrupt_pct, seed=seed):
            archivo.write(linea)
            tamano += len(linea)
            if parsear_linea(linea) is None:
                invalidas += 1
            else:
                validas += 1
    return ResultadoGeneracion(tamano_bytes=tamano, validas=validas, invalidas=invalidas)


def _validar_argumentos(size_mb: int, ips: int, corrupt_pct: float) -> None:
    if size_mb < 0:
        raise ValueError("--size-mb debe ser un entero no negativo")
    if ips <= 0:
        raise ValueError("--ips debe ser un entero positivo")
    if not (0 <= corrupt_pct <= 100):
        raise ValueError("--corrupt-pct debe estar entre 0 y 100")


def _validar_destino_local(destino: str) -> Path:
    partes = urlsplit(destino)
    if partes.scheme == "s3":
        raise NotImplementedError("Destino s3:// todavía no soportado; ver issue #12")
    if partes.scheme in ("", "local"):
        ruta = partes.path if partes.scheme == "local" else destino
        return Path(ruta).expanduser()
    raise ValueError(f"Esquema de destino no soportado: {partes.scheme}")


def _ip_aleatoria(rng: random.Random) -> str:
    return f"{rng.randint(1, 223)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def _formatear_timestamp(instante: datetime) -> str:
    mes = _MESES_INGLES[instante.month - 1]
    offset = instante.utcoffset() or timedelta()
    signo = "+" if offset >= timedelta() else "-"
    minutos_totales = int(abs(offset).total_seconds() // 60)
    return (
        f"{instante.day:02d}/{mes}/{instante.year:04d}:"
        f"{instante.hour:02d}:{instante.minute:02d}:{instante.second:02d} "
        f"{signo}{minutos_totales // 60:02d}{minutos_totales % 60:02d}"
    )


def _linea_valida(rng: random.Random, pool_ips: list[str], instante: datetime) -> bytes:
    ip = rng.choice(pool_ips)
    metodo = rng.choice(_METODOS)
    destino = rng.choice(_DESTINOS)
    estado = rng.choices(
        [codigo for codigo, _ in _ESTADOS_PESOS],
        weights=[peso for _, peso in _ESTADOS_PESOS],
    )[0]
    cuerpo = rng.randint(100, 8000)
    texto = (
        f'{ip} - - [{_formatear_timestamp(instante)}] '
        f'"{metodo} {destino} HTTP/1.1" {estado} {cuerpo} "-" "{_AGENTE}"\n'
    )
    return texto.encode("utf-8")


def _linea_corrupta(rng: random.Random) -> bytes:
    opciones = (
        b"registro corrupto\n",
        f"{rng.randint(1, 999)}.sin.formato.valido - -\n".encode(),
        b'192.168.0.1 - - [fecha-invalida] "GET / HTTP/1.1" 999 - "-" "-"\n',
    )
    return rng.choice(opciones)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m loghunter.generar",
        description="Genera un log combined reproducible (--size-mb y --chunk-mb usan MiB = 2**20 bytes).",
    )
    parser.add_argument(
        "-o", "--out", required=True,
        help="Destino: ruta local, local://... (s3://bucket/key pendiente del issue #12)",
    )
    parser.add_argument(
        "--size-mb", required=True, type=int,
        help="Tamaño objetivo en MiB; 0 genera un archivo vacío",
    )
    parser.add_argument(
        "--ips", type=int, default=5000,
        help="Cantidad de IPs distintas a usar (default: 5000)",
    )
    parser.add_argument(
        "--corrupt-pct", type=float, default=0.0,
        help="Porcentaje aproximado de líneas corruptas, 0-100 (default: 0)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Semilla del PRNG; misma semilla y argumentos producen los mismos bytes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    try:
        resultado = escribir_log(
            destino=args.out,
            size_mb=args.size_mb,
            ips=args.ips,
            corrupt_pct=args.corrupt_pct,
            seed=args.seed,
        )
    except (ValueError, NotImplementedError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(
        f"Generado {resultado.tamano_bytes} bytes en {args.out} "
        f"(validas={resultado.validas}, invalidas={resultado.invalidas}, "
        f"total={resultado.total})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

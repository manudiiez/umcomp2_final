"""Pruebas del generador reproducible (issue #5): PROJECT_STATEMENT §12.3."""

from pathlib import Path

import pytest

from loghunter.generar import escribir_log, generar_log
from loghunter.parser import parsear_linea


def test_tamano_cero_produce_archivo_vacio(tmp_path: Path) -> None:
    destino = tmp_path / "vacio.log"
    resultado = escribir_log(destino=str(destino), size_mb=0, seed=1)
    assert resultado.tamano_bytes == 0
    assert resultado.validas == 0
    assert resultado.invalidas == 0
    assert destino.read_bytes() == b""


def test_misma_semilla_y_argumentos_producen_los_mismos_bytes(tmp_path: Path) -> None:
    destino_a = tmp_path / "a.log"
    destino_b = tmp_path / "b.log"
    escribir_log(destino=str(destino_a), size_mb=1, ips=50, corrupt_pct=5, seed=42)
    escribir_log(destino=str(destino_b), size_mb=1, ips=50, corrupt_pct=5, seed=42)
    assert destino_a.read_bytes() == destino_b.read_bytes()


def test_semillas_distintas_producen_bytes_distintos(tmp_path: Path) -> None:
    destino_a = tmp_path / "a.log"
    destino_b = tmp_path / "b.log"
    escribir_log(destino=str(destino_a), size_mb=1, ips=50, seed=1)
    escribir_log(destino=str(destino_b), size_mb=1, ips=50, seed=2)
    assert destino_a.read_bytes() != destino_b.read_bytes()


def test_alcanza_o_supera_el_tamano_objetivo_completando_la_ultima_linea(
    tmp_path: Path,
) -> None:
    destino = tmp_path / "log.log"
    objetivo = 1 * (2**20)
    resultado = escribir_log(destino=str(destino), size_mb=1, seed=7)
    assert resultado.tamano_bytes >= objetivo
    contenido = destino.read_bytes()
    assert len(contenido) == resultado.tamano_bytes
    # La última línea está completa: termina en salto de línea.
    assert contenido.endswith(b"\n")


def test_archivo_generado_es_procesable_por_el_parser(tmp_path: Path) -> None:
    destino = tmp_path / "log.log"
    resultado = escribir_log(
        destino=str(destino), size_mb=1, ips=20, corrupt_pct=10, seed=3
    )
    validas = invalidas = 0
    for linea in destino.read_bytes().splitlines(keepends=True):
        if parsear_linea(linea) is None:
            invalidas += 1
        else:
            validas += 1
    assert validas == resultado.validas
    assert invalidas == resultado.invalidas
    assert invalidas > 0  # con corrupt_pct=10 debe haber generado alguna


def test_timestamps_monotonos(tmp_path: Path) -> None:
    destino = tmp_path / "log.log"
    escribir_log(destino=str(destino), size_mb=1, ips=10, seed=9)
    instantes = []
    for linea in destino.read_bytes().splitlines(keepends=True):
        registro = parsear_linea(linea)
        if registro is not None:
            instantes.append(registro.timestamp)
    assert instantes == sorted(instantes)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"size_mb": -1},
        {"size_mb": 1, "ips": 0},
        {"size_mb": 1, "corrupt_pct": 101},
        {"size_mb": 1, "corrupt_pct": -1},
    ],
)
def test_argumentos_fuera_de_rango_rechazados(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        list(generar_log(**kwargs))


def test_destino_s3_no_soportado_todavia() -> None:
    with pytest.raises(NotImplementedError):
        escribir_log(destino="s3://logs/access.log", size_mb=1, seed=1)

"""Verificación de integración del núcleo local (issue #8).

Compara el agregado por tramos (`workers.calcular_rangos` + `workers.map_chunk`
+ `consultas.reducir`) contra la referencia secuencial
(`consultas.calcular_referencia`) sobre archivos generados por
`loghunter.generar`, con varios tamaños de tramo no alineados entre sí.

No requiere Redis, MinIO, Celery ni ningún proceso adicional: todo corre en
memoria dentro de un único proceso de pytest (CU-02 a CU-06, CU-11, CU-12,
CU-24; AC-01 a AC-04 y AC-16). Comandos y resultados de referencia en
doc/VERIFICACION_NUCLEO.md.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from loghunter.consultas import CONSULTAS, calcular_referencia, presentar, reducir
from loghunter.generar import escribir_log
from loghunter.workers import calcular_rangos, map_chunk

_EJEMPLO_PROJECT_STATEMENT = (
    b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"\n'
    b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"\n'
    b'10.0.0.1 - - [20/Oct/2026:14:10:00 -0300] "GET /a HTTP/1.1" 200 50 "-" "demo"\n'
    b'10.0.0.2 - - [20/Oct/2026:15:00:00 -0300] "GET /a?x=1 HTTP/1.1" 500 - "-" "demo"\n'
    b'10.0.0.1 - - [20/Oct/2026:15:15:00 -0300] "GET /a HTTP/1.1" 200 300 "-" "demo"\n'
    b"registro corrupto\n"
)


def _verificar_equivalencia(archivo: Path, chunk_bytes: int) -> None:
    """Para las cuatro consultas: map-reduce por tramos == referencia secuencial."""
    uri = str(archivo)
    tamano = archivo.stat().st_size
    for query in CONSULTAS:
        parciales = []
        validas_total = 0
        invalidas_total = 0
        for inicio, fin in calcular_rangos(tamano, chunk_bytes):
            resultado = map_chunk(uri, inicio, fin, query)
            parciales.append(Counter(resultado["contador"]))
            validas_total += resultado["validas"]
            invalidas_total += resultado["invalidas"]

        total = reducir(parciales)
        contador_ref, validas_ref, invalidas_ref = calcular_referencia(archivo, query)

        assert presentar(query, total) == presentar(query, contador_ref), query
        assert validas_total == validas_ref, query
        assert invalidas_total == invalidas_ref, query


@pytest.mark.parametrize("chunk_bytes", [64 * 1024, 1 * 2**20, 700_003])
def test_log_generado_coincide_con_referencia_en_varios_tamanos_de_tramo(
    tmp_path: Path, chunk_bytes: int
) -> None:
    """Datos conocidos (generador reproducible) con tramos variados, incluido
    uno deliberadamente no alineado a ninguna potencia de 2 (700 003 bytes)."""
    archivo = tmp_path / "access.log"
    escribir_log(destino=str(archivo), size_mb=2, ips=200, corrupt_pct=5, seed=20261109)
    _verificar_equivalencia(archivo, chunk_bytes)


def test_archivo_vacio_no_genera_tareas_y_coincide_con_referencia(tmp_path: Path) -> None:
    archivo = tmp_path / "vacio.log"
    escribir_log(destino=str(archivo), size_mb=0, seed=1)
    assert archivo.stat().st_size == 0
    assert calcular_rangos(0, 64 * 1024) == []
    _verificar_equivalencia(archivo, 64 * 1024)


def test_archivo_totalmente_invalido_no_aporta_a_ninguna_consulta(tmp_path: Path) -> None:
    archivo = tmp_path / "todo_invalido.log"
    archivo.write_bytes(b"\n".join([b"esto no es un registro combined"] * 500) + b"\n")

    _verificar_equivalencia(archivo, 777)  # tramo de tamaño arbitrario, no alineado

    contador, validas, invalidas = calcular_referencia(archivo, "status")
    assert validas == 0
    assert invalidas == 500
    assert contador == {}


def test_ejemplo_normativo_con_tramos_de_un_byte(tmp_path: Path) -> None:
    """Entrada límite: cada tramo cabe en una fracción de un solo registro."""
    archivo = tmp_path / "normativo.log"
    archivo.write_bytes(_EJEMPLO_PROJECT_STATEMENT)

    _verificar_equivalencia(archivo, 1)

    _, validas, invalidas = calcular_referencia(archivo, "top-ips")
    assert validas == 5
    assert invalidas == 1

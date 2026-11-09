"""Pruebas de agregadores y referencia secuencial (issue #4): PROJECT_STATEMENT §7.2-7.3."""

from pathlib import Path

import pytest

from loghunter.consultas import agregar_parcial, calcular_referencia, presentar, reducir
from loghunter.parser import parsear_linea

_LINEAS_EJEMPLO = [
    b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"',
    b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"',
    b'10.0.0.1 - - [20/Oct/2026:14:10:00 -0300] "GET /a HTTP/1.1" 200 50 "-" "demo"',
    b'10.0.0.2 - - [20/Oct/2026:15:00:00 -0300] "GET /a?x=1 HTTP/1.1" 500 - "-" "demo"',
    b'10.0.0.1 - - [20/Oct/2026:15:15:00 -0300] "GET /a HTTP/1.1" 200 300 "-" "demo"',
    b"registro corrupto",
]


def _registros_ejemplo() -> list:
    return [r for linea in _LINEAS_EJEMPLO if (r := parsear_linea(linea)) is not None]


def test_top_ips_coincide_con_el_ejemplo_normativo() -> None:
    contador = agregar_parcial("top-ips", _registros_ejemplo())
    assert presentar("top-ips", contador) == [("10.0.0.1", 3), ("10.0.0.2", 2)]


def test_status_muestra_todos_los_codigos_en_orden_numerico() -> None:
    contador = agregar_parcial("status", _registros_ejemplo())
    assert presentar("status", contador) == [("200", 3), ("404", 1), ("500", 1)]


def test_urls_no_fusiona_query_strings_distintos() -> None:
    contador = agregar_parcial("urls", _registros_ejemplo())
    assert presentar("urls", contador) == [("/a", 3), ("/a?x=1", 1), ("/b", 1)]


def test_traffic_agrupa_por_fecha_hora_utc() -> None:
    contador = agregar_parcial("traffic", _registros_ejemplo())
    assert presentar("traffic", contador) == [
        ("2026-10-20T17:00:00Z", 350),
        ("2026-10-20T18:00:00Z", 300),
    ]


def test_top_recorta_solo_top_ips_y_urls() -> None:
    contador = agregar_parcial("top-ips", _registros_ejemplo())
    assert presentar("top-ips", contador, top=1) == [("10.0.0.1", 3)]
    # status y traffic no se recortan aunque se pase top
    contador_status = agregar_parcial("status", _registros_ejemplo())
    assert len(presentar("status", contador_status, top=1)) == 3


def test_distintos_dias_no_se_mezclan_en_traffic() -> None:
    lineas = [
        b'10.0.0.1 - - [20/Oct/2026:23:30:00 +0000] "GET /a HTTP/1.1" 200 10 "-" "-"',
        b'10.0.0.1 - - [21/Oct/2026:00:30:00 +0000] "GET /a HTTP/1.1" 200 20 "-" "-"',
    ]
    registros = [parsear_linea(linea) for linea in lineas]
    contador = agregar_parcial("traffic", registros)
    assert presentar("traffic", contador) == [
        ("2026-10-20T23:00:00Z", 10),
        ("2026-10-21T00:00:00Z", 20),
    ]


def test_desempate_en_top_es_por_clave_ascendente() -> None:
    lineas = [
        b'10.0.0.5 - - [01/Jan/2026:00:00:00 +0000] "GET /a HTTP/1.1" 200 1 "-" "-"',
        b'10.0.0.2 - - [01/Jan/2026:00:00:01 +0000] "GET /b HTTP/1.1" 200 1 "-" "-"',
    ]
    registros = [parsear_linea(linea) for linea in lineas]
    contador = agregar_parcial("top-ips", registros)
    # Ambas con 1 petición: debe ganar la IP menor.
    assert presentar("top-ips", contador) == [("10.0.0.2", 1), ("10.0.0.5", 1)]


def test_top_global_difiere_de_tops_locales_se_resuelve_con_reduce() -> None:
    """El top global de un reduce puede no aparecer primero en ningún tramo local."""
    # Tramo A: IP X dos veces (sería top local de A), IP Z una vez.
    tramo_a = [
        b'10.0.0.9 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "-"',
        b'10.0.0.9 - - [01/Jan/2026:00:00:01 +0000] "GET / HTTP/1.1" 200 1 "-" "-"',
        b'10.0.0.1 - - [01/Jan/2026:00:00:02 +0000] "GET / HTTP/1.1" 200 1 "-" "-"',
    ]
    # Tramo B: IP Z dos veces más (sería top local de B), IP X ninguna.
    tramo_b = [
        b'10.0.0.1 - - [01/Jan/2026:00:00:03 +0000] "GET / HTTP/1.1" 200 1 "-" "-"',
        b'10.0.0.1 - - [01/Jan/2026:00:00:04 +0000] "GET / HTTP/1.1" 200 1 "-" "-"',
    ]
    parcial_a = agregar_parcial("top-ips", [parsear_linea(l) for l in tramo_a])
    parcial_b = agregar_parcial("top-ips", [parsear_linea(l) for l in tramo_b])
    total = reducir([parcial_a, parcial_b])
    # 10.0.0.1 suma 1+2=3 en total y gana, aunque en ningún tramo individual
    # haya sido el máximo local (tramo A: 10.0.0.9 con 2; tramo B: 10.0.0.1 con 2).
    assert presentar("top-ips", total, top=1) == [("10.0.0.1", 3)]


def test_consulta_desconocida_rechazada() -> None:
    with pytest.raises(ValueError):
        agregar_parcial("desconocida", [])
    with pytest.raises(ValueError):
        presentar("desconocida", {})


def test_referencia_secuencial_coincide_con_agregado_en_memoria(tmp_path: Path) -> None:
    archivo = tmp_path / "access.log"
    archivo.write_bytes(b"\n".join(_LINEAS_EJEMPLO) + b"\n")

    for query in ("top-ips", "status", "urls", "traffic"):
        contador_ref, validas, invalidas = calcular_referencia(archivo, query)
        contador_memoria = agregar_parcial(query, _registros_ejemplo())
        assert presentar(query, contador_ref) == presentar(query, contador_memoria)
        assert validas == 5
        assert invalidas == 1


def test_referencia_secuencial_sobre_archivo_vacio(tmp_path: Path) -> None:
    archivo = tmp_path / "vacio.log"
    archivo.write_bytes(b"")
    contador, validas, invalidas = calcular_referencia(archivo, "status")
    assert contador == {}
    assert validas == 0
    assert invalidas == 0

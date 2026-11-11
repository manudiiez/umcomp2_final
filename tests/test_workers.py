"""Pruebas del núcleo de map-reduce (issue #7): PROJECT_STATEMENT §8 y ADR-0005."""

from collections import Counter
from pathlib import Path

import pytest

from loghunter import lector as lector_mod
from loghunter import workers
from loghunter.consultas import calcular_referencia, presentar, reducir
from loghunter.workers import _lineas_del_tramo, calcular_rangos, map_chunk

MIB = 2**20
GIB = 2**30


# --- calcular_rangos: PROJECT_STATEMENT §8.1 -------------------------------


def test_128mib_con_tramos_de_32mib_produce_cuatro_rangos() -> None:
    rangos = calcular_rangos(128 * MIB, 32 * MIB)
    assert rangos == [
        (0, 33554432),
        (33554432, 67108864),
        (67108864, 100663296),
        (100663296, 134217728),
    ]


def test_2gib_con_tramos_de_32mib_produce_64_rangos() -> None:
    rangos = calcular_rangos(2 * GIB, 32 * MIB)
    assert len(rangos) == 64
    assert rangos[0] == (0, 32 * MIB)
    assert rangos[-1][1] == 2 * GIB


def test_archivo_vacio_no_genera_rangos() -> None:
    assert calcular_rangos(0, 32 * MIB) == []


def test_chunk_no_positivo_rechazado() -> None:
    with pytest.raises(ValueError):
        calcular_rangos(100, 0)
    with pytest.raises(ValueError):
        calcular_rangos(100, -1)


# --- Partición a fin de línea: PROJECT_STATEMENT §8.2 / ADR-0005 -----------


def _lineas_en_tramo(ruta: Path, inicio: int, fin: int) -> list[bytes]:
    with lector_mod.abrir(str(ruta)) as lector:
        return list(_lineas_del_tramo(lector, inicio, fin))


def test_corte_exactamente_despues_de_lf_no_pierde_ni_duplica(tmp_path: Path) -> None:
    contenido = b"AAAA\nBBBBBB\nCC\n"  # líneas en [0,5) [5,12) [12,15)
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    assert _lineas_en_tramo(archivo, 0, 5) == [b"AAAA\n"]
    assert _lineas_en_tramo(archivo, 5, 12) == [b"BBBBBB\n"]
    assert _lineas_en_tramo(archivo, 12, 15) == [b"CC\n"]


def test_corte_dentro_de_una_linea_no_pierde_ni_duplica(tmp_path: Path) -> None:
    contenido = b"AAAA\nBBBBBB\nCC\n"
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    # Tramo [0, 8): corta "BBBBBB\n" a la mitad; la línea completa se cuenta
    # en el primer tramo (contiene su inicio en offset 5).
    assert _lineas_en_tramo(archivo, 0, 8) == [b"AAAA\n", b"BBBBBB\n"]
    # El tramo siguiente empieza en 8 (dentro de BBBBBB\n): no vuelve a contarla.
    assert _lineas_en_tramo(archivo, 8, 15) == [b"CC\n"]


def test_corte_justo_despues_del_ultimo_byte_del_archivo(tmp_path: Path) -> None:
    contenido = b"AAAA\nBBBBBB\n"
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    assert _lineas_en_tramo(archivo, 5, len(contenido)) == [b"BBBBBB\n"]
    assert _lineas_en_tramo(archivo, len(contenido), len(contenido)) == []


def test_corte_entre_cr_y_lf_no_pierde_ni_duplica(tmp_path: Path) -> None:
    contenido = b"AAAA\r\nBBBB\r\n"  # CRLF: \r en offset 4, \n en offset 5
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    # Tramo que corta justo entre \r (offset 4) y \n (offset 5): el \r no es
    # LF, así que inicio=5 no está alineado y se descarta la cola de la
    # primera línea; el segundo tramo arranca en "BBBB\r\n".
    assert _lineas_en_tramo(archivo, 0, 5) == [b"AAAA\r\n"]
    assert _lineas_en_tramo(archivo, 5, len(contenido)) == [b"BBBB\r\n"]


def test_corte_sobre_caracter_multibyte_utf8_no_corrompe_la_linea(tmp_path: Path) -> None:
    linea1 = "año-ñandú\n".encode("utf-8")  # contiene bytes multibyte
    linea2 = b"ascii\n"
    contenido = linea1 + linea2
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    # Cortar en medio de los bytes multibyte de linea1 no debe alterar el
    # agregado: la línea completa se cuenta en el tramo que contiene su inicio.
    punto_medio = len(linea1) - 2  # cae dentro de un carácter multibyte
    assert _lineas_en_tramo(archivo, 0, punto_medio) == [linea1]
    assert _lineas_en_tramo(archivo, punto_medio, len(contenido)) == [linea2]


def test_ultima_linea_sin_salto_final_se_cuenta(tmp_path: Path) -> None:
    contenido = b"AAAA\nsin_salto_final"
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    assert _lineas_en_tramo(archivo, 0, len(contenido)) == [
        b"AAAA\n",
        b"sin_salto_final",
    ]


def test_linea_mayor_a_64kib_se_lee_completa(tmp_path: Path) -> None:
    linea_larga = (b"x" * 70_000) + b"\n"
    contenido = b"corta\n" + linea_larga + b"final\n"
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    assert _lineas_en_tramo(archivo, 0, len(contenido)) == [
        b"corta\n",
        linea_larga,
        b"final\n",
    ]


def test_linea_que_atraviesa_varios_tramos_se_cuenta_una_sola_vez(tmp_path: Path) -> None:
    linea_larga = (b"y" * 200_000) + b"\n"  # atraviesa varios tramos de 64 KiB
    contenido = b"inicio\n" + linea_larga
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    inicio_linea_larga = len(b"inicio\n")
    # Tramo que solo contiene el inicio de la línea larga: la cuenta entera.
    assert _lineas_en_tramo(
        archivo, inicio_linea_larga, inicio_linea_larga + 10
    ) == [linea_larga]
    # Un tramo posterior, totalmente dentro de la línea larga, no la recuenta.
    assert _lineas_en_tramo(
        archivo, inicio_linea_larga + 50_000, inicio_linea_larga + 60_000
    ) == []


def test_tramo_sin_ningun_inicio_de_linea_no_produce_registros(tmp_path: Path) -> None:
    contenido = b"AAAAAAAAAA\nBBBBBBBBBB\n"
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(contenido)
    # [2, 9) cae enteramente dentro de la primera línea, sin alcanzar su LF.
    assert _lineas_en_tramo(archivo, 2, 9) == []


# --- map_chunk: idempotencia y conteos completos ----------------------------


def test_map_chunk_es_idempotente(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(
        b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"\n'
        b"invalida\n"
    )
    uri = str(archivo)
    r1 = map_chunk(uri, 0, archivo.stat().st_size, "top-ips")
    r2 = map_chunk(uri, 0, archivo.stat().st_size, "top-ips")
    assert r1 == r2 == {"contador": {"10.0.0.1": 1}, "validas": 1, "invalidas": 1}


def test_map_chunk_sobre_tramo_vacio_no_produce_registros(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] \"GET /a HTTP/1.1\" 200 1 \"-\" \"-\"\n")
    resultado = map_chunk(str(archivo), 0, 0, "status")
    assert resultado == {"contador": {}, "validas": 0, "invalidas": 0}


# --- Integración: distintos tamaños de tramo coinciden con la referencia ---

_EJEMPLO_PROJECT_STATEMENT = (
    b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"\n'
    b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"\n'
    b'10.0.0.1 - - [20/Oct/2026:14:10:00 -0300] "GET /a HTTP/1.1" 200 50 "-" "demo"\n'
    b'10.0.0.2 - - [20/Oct/2026:15:00:00 -0300] "GET /a?x=1 HTTP/1.1" 500 - "-" "demo"\n'
    b'10.0.0.1 - - [20/Oct/2026:15:15:00 -0300] "GET /a HTTP/1.1" 200 300 "-" "demo"\n'
    b"registro corrupto\n"
)


@pytest.mark.parametrize("chunk_bytes", [1, 5, 13, 37, 1000])
@pytest.mark.parametrize("query", ["top-ips", "status", "urls", "traffic"])
def test_map_reduce_coincide_con_referencia_para_varios_tamanos_de_tramo(
    tmp_path: Path, chunk_bytes: int, query: str
) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(_EJEMPLO_PROJECT_STATEMENT)
    uri = str(archivo)
    tamano = archivo.stat().st_size

    parciales = []
    validas_total = 0
    invalidas_total = 0
    for inicio, fin in calcular_rangos(tamano, chunk_bytes):
        resultado = map_chunk(uri, inicio, fin, query)
        parciales.append(resultado["contador"])
        validas_total += resultado["validas"]
        invalidas_total += resultado["invalidas"]

    total = reducir(Counter(p) for p in parciales)

    contador_ref, validas_ref, invalidas_ref = calcular_referencia(archivo, query)
    assert presentar(query, total) == presentar(query, contador_ref)
    assert validas_total == validas_ref == 5
    assert invalidas_total == invalidas_ref == 1

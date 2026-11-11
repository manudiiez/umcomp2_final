"""Pruebas del lector local por rangos (issue #6): PROJECT_STATEMENT §6 y §8.3."""

import mmap
from pathlib import Path
from unittest.mock import patch

import pytest

from loghunter import lector


def test_tamano_informa_el_tamano_real(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"0123456789")
    with lector.abrir(str(archivo)) as l:
        assert l.tamano() == 10


def test_leer_rango_no_alineado_devuelve_bytes_exactos(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"abcdefghij")
    with lector.abrir(str(archivo)) as l:
        assert l.leer(2, 5) == b"cde"
        assert l.leer(0, 10) == b"abcdefghij"
        assert l.leer(9, 10) == b"j"
        assert l.leer(0, 0) == b""
        assert l.leer(10, 10) == b""


def test_leer_rango_alineado_a_granularidad(tmp_path: Path) -> None:
    """Fuerza offsets no múltiplos de ALLOCATIONGRANULARITY para validar el ajuste interno."""
    granularidad = mmap.ALLOCATIONGRANULARITY
    contenido = bytes((i % 256 for i in range(granularidad * 2 + 100)))
    archivo = tmp_path / "grande.bin"
    archivo.write_bytes(contenido)
    with lector.abrir(str(archivo)) as l:
        inicio = granularidad + 7
        fin = granularidad + 37
        assert l.leer(inicio, fin) == contenido[inicio:fin]
        # Rango que atraviesa dos bloques de granularidad.
        inicio2 = granularidad - 5
        fin2 = granularidad + 5
        assert l.leer(inicio2, fin2) == contenido[inicio2:fin2]


def test_leer_hasta_salto_devuelve_hasta_lf_inclusive(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"linea1\nlinea2\n")
    with lector.abrir(str(archivo)) as l:
        assert l.leer_hasta_salto(0, 1024) == b"linea1\n"
        assert l.leer_hasta_salto(7, 1024) == b"linea2\n"


def test_leer_hasta_salto_sin_lf_devuelve_hasta_limite_o_eof(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"sin_salto_final")
    with lector.abrir(str(archivo)) as l:
        assert l.leer_hasta_salto(0, 1024) == b"sin_salto_final"
        assert l.leer_hasta_salto(0, 4) == b"sin_"


def test_rango_fuera_del_archivo_es_rechazado(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"corto")
    with lector.abrir(str(archivo)) as l:
        with pytest.raises(ValueError):
            l.leer(0, 100)
        with pytest.raises(ValueError):
            l.leer(-1, 2)
        with pytest.raises(ValueError):
            l.leer(3, 1)


def test_archivo_inexistente_es_rechazado(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        lector.abrir(str(tmp_path / "no_existe.txt"))


def test_archivo_vacio_no_se_mapea_y_tamano_es_cero(tmp_path: Path) -> None:
    archivo = tmp_path / "vacio.txt"
    archivo.write_bytes(b"")
    with patch("mmap.mmap", side_effect=AssertionError("no debe mapear un archivo vacío")):
        with lector.abrir(str(archivo)) as l:
            assert l.tamano() == 0
            assert l.leer(0, 0) == b""


def test_cerrar_es_idempotente(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"datos")
    l = lector.abrir(str(archivo))
    l.cerrar()
    l.cerrar()  # no debe lanzar ni volver a cerrar el mismo fd


def test_backend_desconocido_es_rechazado() -> None:
    with pytest.raises(ValueError):
        lector.abrir("ftp://logs/access.log")


def test_normalizar_uri_local_acepta_ruta_plana_y_local_scheme(tmp_path: Path) -> None:
    archivo = tmp_path / "log.txt"
    archivo.write_bytes(b"x")
    esperado = f"local://{archivo.resolve()}"
    assert lector.normalizar_uri_local(str(archivo)) == esperado
    assert lector.normalizar_uri_local(esperado) == esperado

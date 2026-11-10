"""Pruebas del generador reproducible (issue #5) y su subida a S3 (issue #12).

PROJECT_STATEMENT §12.3; AC-16. La subida real a MinIO se verifica con
infraestructura real en el issue #14; aquí se inyecta un cliente S3 doble
(mismo enfoque que `tests/test_lector_s3.py`, por la incompatibilidad de
`moto` documentada ahí), sin red ni dependencias externas.
"""

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from loghunter.generar import _FlujoGenerado, escribir_log, generar_log
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


class _ClienteFakeSubida:
    """Doble del cliente boto3 S3: solo `upload_fileobj`/`close`, en memoria."""

    def __init__(self, buckets: set[str]) -> None:
        self._buckets = buckets
        self.objetos: dict[tuple[str, str], bytes] = {}
        self.cerrado = False

    def upload_fileobj(self, Fileobj, Bucket: str, Key: str) -> None:
        if Bucket not in self._buckets:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchBucket", "Message": "x"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "PutObject",
            )
        partes = []
        while True:
            # Tamaño de bloque deliberadamente chico y no alineado a líneas,
            # para ejercitar el buffer interno de _FlujoGenerado.
            bloque = Fileobj.read(37)
            if not bloque:
                break
            partes.append(bloque)
        self.objetos[(Bucket, Key)] = b"".join(partes)

    def close(self) -> None:
        self.cerrado = True


def _parchear_cliente_s3(monkeypatch: pytest.MonkeyPatch, fake: _ClienteFakeSubida) -> None:
    import loghunter.generar as generar_mod

    monkeypatch.setattr(generar_mod, "cliente_s3", lambda: fake)


def test_subida_s3_produce_los_mismos_bytes_que_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destino_local = tmp_path / "local.log"
    escribir_log(destino=str(destino_local), size_mb=1, ips=30, corrupt_pct=5, seed=42)

    fake = _ClienteFakeSubida(buckets={"logs"})
    _parchear_cliente_s3(monkeypatch, fake)
    resultado = escribir_log(
        destino="s3://logs/access.log", size_mb=1, ips=30, corrupt_pct=5, seed=42
    )

    assert fake.objetos[("logs", "access.log")] == destino_local.read_bytes()
    assert fake.cerrado is True
    assert resultado.tamano_bytes == destino_local.stat().st_size


def test_subida_s3_cuenta_validas_e_invalidas_en_una_sola_pasada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _ClienteFakeSubida(buckets={"logs"})
    _parchear_cliente_s3(monkeypatch, fake)
    resultado = escribir_log(
        destino="s3://logs/access.log", size_mb=1, ips=20, corrupt_pct=10, seed=3
    )

    subido = fake.objetos[("logs", "access.log")]
    validas = invalidas = 0
    for linea in subido.splitlines(keepends=True):
        if parsear_linea(linea) is None:
            invalidas += 1
        else:
            validas += 1
    assert resultado.validas == validas
    assert resultado.invalidas == invalidas
    assert invalidas > 0


def test_subida_s3_tamano_cero_sube_objeto_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ClienteFakeSubida(buckets={"logs"})
    _parchear_cliente_s3(monkeypatch, fake)
    resultado = escribir_log(destino="s3://logs/vacio.log", size_mb=0, seed=1)
    assert fake.objetos[("logs", "vacio.log")] == b""
    assert resultado.tamano_bytes == 0


def test_subida_s3_bucket_inexistente_es_rechazada(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _ClienteFakeSubida(buckets=set())
    _parchear_cliente_s3(monkeypatch, fake)
    with pytest.raises(FileNotFoundError):
        escribir_log(destino="s3://no-existe/access.log", size_mb=1, seed=1)


def test_flujo_generado_lee_lo_mismo_sin_importar_el_tamano_de_bloque() -> None:
    def _lineas():
        yield b"abc\n"
        yield b"defgh\n"
        yield b"i\n"

    flujo = _FlujoGenerado(iter(_lineas()))
    partes = []
    while True:
        bloque = flujo.read(2)
        if not bloque:
            break
        partes.append(bloque)
    assert b"".join(partes) == b"abc\ndefgh\ni\n"
    assert flujo.tamano == len(b"abc\ndefgh\ni\n")
    assert flujo.validas == 0 and flujo.invalidas == 3  # ninguna es un registro combined válido


def test_flujo_generado_read_sin_tamano_devuelve_todo() -> None:
    flujo = _FlujoGenerado(iter([b"x" * 5, b"y" * 5]))
    assert flujo.read(-1) == b"x" * 5 + b"y" * 5
    assert flujo.read(-1) == b""

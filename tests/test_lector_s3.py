"""Pruebas del backend S3 del lector por rangos (issue #11).

PROJECT_STATEMENT §§6, 8.3 y 9.3; ADR-0004. Se probó primero con `moto`
para simular MinIO en memoria, pero su versión disponible (5.2.5) no
intercepta correctamente las llamadas con la versión de `botocore` que
trae `boto3` en este entorno (`create_bucket`/`head_object` devuelven un
404 que no viene de AWS ni de MinIO, sino de la propia interceptación
rota) — se descartó esa dependencia. En su lugar, estas pruebas inyectan
un cliente S3 doble (`_ClienteFakeS3`) que implementa solo las operaciones
que usa `LectorS3` (`head_object`, `get_object`, `close`), sin red ni
dependencias externas. La equivalencia de bytes contra un MinIO real y un
worker en otro host se verifica con infraestructura real en el issue #14.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from loghunter import lector
from loghunter.lector import AlmacenamientoNoDisponible, normalizar_uri_s3

_BUCKET = "logs"
_KEY = "access.log"
_CONTENIDO = b"linea1\nlinea2\nlinea3-sin-salto"


def _client_error(codigo: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": codigo, "Message": "x"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


class _CuerpoFake:
    """Doble del `StreamingBody` que devuelve `get_object`."""

    def __init__(self, datos: bytes) -> None:
        self._datos = datos
        self.cerrado = False

    def read(self) -> bytes:
        return self._datos

    def close(self) -> None:
        self.cerrado = True


class _ClienteFakeS3:
    """Doble de un cliente `boto3` S3: solo las operaciones que usa `LectorS3`."""

    def __init__(self, objetos: dict[tuple[str, str], bytes]) -> None:
        self._objetos = objetos
        self.cerrado = False
        self.llamadas_get = 0

    def head_object(self, Bucket: str, Key: str) -> dict:
        self._verificar(Bucket, Key)
        return {"ContentLength": len(self._objetos[(Bucket, Key)])}

    def get_object(self, Bucket: str, Key: str, Range: str) -> dict:
        self.llamadas_get += 1
        self._verificar(Bucket, Key)
        datos = self._objetos[(Bucket, Key)]
        inicio_s, fin_incl_s = Range.removeprefix("bytes=").split("-")
        inicio, fin_incl = int(inicio_s), int(fin_incl_s)
        return {"Body": _CuerpoFake(datos[inicio : fin_incl + 1])}

    def close(self) -> None:
        self.cerrado = True

    def _verificar(self, bucket: str, key: str) -> None:
        if (bucket, key) not in self._objetos:
            raise _client_error("NoSuchKey", 404)


class _ClienteAccesoDenegado:
    """Doble que siempre rechaza con 403, para cubrir el caso permanente."""

    def head_object(self, Bucket: str, Key: str) -> dict:
        raise _client_error("AccessDenied", 403)

    def close(self) -> None:
        pass


def _abrir_con_fake(monkeypatch: pytest.MonkeyPatch, objetos: dict[tuple[str, str], bytes]):
    import loghunter.lector as lector_mod

    monkeypatch.setattr(lector_mod, "_cliente_s3", lambda: _ClienteFakeS3(objetos))
    return lector.abrir(f"s3://{_BUCKET}/{_KEY}")


# --- normalizar_uri_s3 ---------------------------------------------------


def test_normalizar_uri_s3_separa_bucket_y_key() -> None:
    assert normalizar_uri_s3("s3://logs/access.log") == ("logs", "access.log")
    assert normalizar_uri_s3("s3://logs/carpeta/access.log") == (
        "logs",
        "carpeta/access.log",
    )


def test_normalizar_uri_s3_sin_key_es_invalida() -> None:
    with pytest.raises(ValueError):
        normalizar_uri_s3("s3://logs")
    with pytest.raises(ValueError):
        normalizar_uri_s3("s3:///access.log")


# --- configuración del cliente real (sin red: solo construcción) --------


def test_cliente_s3_usa_endpoint_region_y_direccionamiento_por_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loghunter.lector as lector_mod

    monkeypatch.setenv("S3_ENDPOINT", "http://minio.local:9000")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")

    cliente = lector_mod._cliente_s3()

    assert cliente.meta.endpoint_url == "http://minio.local:9000"
    assert cliente.meta.region_name == "sa-east-1"
    assert cliente.meta.config.s3["addressing_style"] == "path"
    assert cliente.meta.config.connect_timeout == lector_mod._S3_CONNECT_TIMEOUT
    assert cliente.meta.config.read_timeout == lector_mod._S3_READ_TIMEOUT


# --- tamano/leer/leer_hasta_salto ----------------------------------------


def test_tamano_informa_el_tamano_real(monkeypatch: pytest.MonkeyPatch) -> None:
    with _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}) as l:
        assert l.tamano() == len(_CONTENIDO)


def test_leer_rango_devuelve_bytes_exactos(monkeypatch: pytest.MonkeyPatch) -> None:
    with _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}) as l:
        assert l.leer(0, 6) == b"linea1"
        assert l.leer(7, 13) == b"linea2"
        assert l.leer(0, len(_CONTENIDO)) == _CONTENIDO
        assert l.leer(0, 0) == b""
        assert l.leer(len(_CONTENIDO), len(_CONTENIDO)) == b""


def test_leer_rango_vacio_no_llama_a_get_object(monkeypatch: pytest.MonkeyPatch) -> None:
    import loghunter.lector as lector_mod

    fake = _ClienteFakeS3({(_BUCKET, _KEY): _CONTENIDO})
    monkeypatch.setattr(lector_mod, "_cliente_s3", lambda: fake)
    with lector.abrir(f"s3://{_BUCKET}/{_KEY}") as l:
        l.leer(3, 3)
    assert fake.llamadas_get == 0


def test_leer_hasta_salto_devuelve_hasta_lf_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    with _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}) as l:
        assert l.leer_hasta_salto(0, 1024) == b"linea1\n"
        assert l.leer_hasta_salto(7, 1024) == b"linea2\n"


def test_leer_hasta_salto_sin_lf_devuelve_hasta_limite_o_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}) as l:
        assert l.leer_hasta_salto(14, 1024) == b"linea3-sin-salto"
        assert l.leer_hasta_salto(14, 4) == b"line"


def test_rango_fuera_del_objeto_es_rechazado(monkeypatch: pytest.MonkeyPatch) -> None:
    with _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}) as l:
        with pytest.raises(ValueError):
            l.leer(0, len(_CONTENIDO) + 1)
        with pytest.raises(ValueError):
            l.leer(-1, 2)
        with pytest.raises(ValueError):
            l.leer(3, 1)


def test_cerrar_es_idempotente(monkeypatch: pytest.MonkeyPatch) -> None:
    l = _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO})
    l.__exit__(None, None, None)
    l.cerrar()
    l.cerrar()


# --- errores permanentes: objeto/bucket ausente y acceso denegado --------


def test_objeto_inexistente_es_rechazado(monkeypatch: pytest.MonkeyPatch) -> None:
    import loghunter.lector as lector_mod

    monkeypatch.setattr(lector_mod, "_cliente_s3", lambda: _ClienteFakeS3({}))
    with pytest.raises(FileNotFoundError):
        lector.abrir(f"s3://{_BUCKET}/no-existe.log")


def test_acceso_denegado_es_rechazado(monkeypatch: pytest.MonkeyPatch) -> None:
    import loghunter.lector as lector_mod

    monkeypatch.setattr(lector_mod, "_cliente_s3", lambda: _ClienteAccesoDenegado())
    with pytest.raises(PermissionError):
        lector.abrir(f"s3://{_BUCKET}/{_KEY}")


# --- equivalencia con el backend local (AC-02) ---------------------------


def test_mismos_rangos_producen_los_mismos_bytes_que_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archivo = tmp_path / _KEY
    archivo.write_bytes(_CONTENIDO)

    with lector.abrir(str(archivo)) as local, _abrir_con_fake(
        monkeypatch, {(_BUCKET, _KEY): _CONTENIDO}
    ) as remoto:
        assert local.tamano() == remoto.tamano()
        for inicio, fin in ((0, 6), (6, 13), (0, len(_CONTENIDO)), (3, 20)):
            assert local.leer(inicio, fin) == remoto.leer(inicio, fin)


# --- clasificación de errores transitorios/permanentes -------------------


@pytest.mark.parametrize(
    "codigo,status,transitorio",
    [
        ("NoSuchKey", 404, False),
        ("NoSuchBucket", 404, False),
        ("AccessDenied", 403, False),
        ("SlowDown", 503, True),
        ("InternalError", 500, True),
        ("RequestTimeout", 400, True),
    ],
)
def test_es_transitorio_distingue_permanentes_de_transitorios(
    codigo: str, status: int, transitorio: bool
) -> None:
    from loghunter.lector import _es_transitorio

    assert _es_transitorio(_client_error(codigo, status)) is transitorio


def test_reintentos_agotados_levantan_almacenamiento_no_disponible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loghunter.lector as lector_mod

    monkeypatch.setattr(lector_mod.time, "sleep", lambda _segundos: None)
    l = _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO})

    def _siempre_transitorio():
        raise _client_error("SlowDown", 503)

    with pytest.raises(AlmacenamientoNoDisponible):
        l._operacion_con_reintentos(_siempre_transitorio)


def test_reintentos_se_recuperan_tras_un_fallo_transitorio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loghunter.lector as lector_mod

    monkeypatch.setattr(lector_mod.time, "sleep", lambda _segundos: None)
    l = _abrir_con_fake(monkeypatch, {(_BUCKET, _KEY): _CONTENIDO})

    intentos = {"n": 0}

    def _falla_una_vez():
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise _client_error("InternalError", 500)
        return "ok"

    assert l._operacion_con_reintentos(_falla_una_vez) == "ok"
    assert intentos["n"] == 2

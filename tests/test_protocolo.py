"""Pruebas del protocolo JSON por línea (issue #15): PROJECT_STATEMENT §10, ADR-0006.

Cubre framing fragmentado/junto, límites de tamaño y tiempo, y el rechazo
estructural de versión/tipo/consulta/URI exigido por AC-15.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from loghunter import protocolo


def _reader_con(datos: bytes, *, limite: int = protocolo.LIMITE_PEDIDO) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=limite)
    reader.feed_data(datos)
    reader.feed_eof()
    return reader


async def _leer_async(datos: bytes, **kwargs) -> dict:
    return await protocolo.leer_pedido(_reader_con(datos), **kwargs)


def _leer(datos: bytes, **kwargs) -> dict:
    return asyncio.run(_leer_async(datos, **kwargs))


def _pedido(**overrides) -> dict:
    base = {"version": 1, "type": "analyze", "file": "local:///tmp/access.log", "query": "status"}
    base.update(overrides)
    return base


def test_codificar_termina_en_lf_y_es_json_valido() -> None:
    crudo = protocolo.codificar({"a": 1})
    assert crudo.endswith(b"\n")
    assert json.loads(crudo.decode("utf-8")) == {"a": 1}


def test_leer_pedido_acepta_un_mensaje_bien_formado() -> None:
    pedido = _leer(protocolo.codificar(_pedido()))
    assert pedido == {
        "version": 1,
        "type": "analyze",
        "file": "local:///tmp/access.log",
        "query": "status",
        "top": 10,
        "chunk_mb": None,
    }


def test_leer_pedido_respeta_top_y_chunk_mb_explicitos() -> None:
    pedido = _leer(protocolo.codificar(_pedido(top=3, chunk_mb=64)))
    assert pedido["top"] == 3
    assert pedido["chunk_mb"] == 64


def test_leer_pedido_funciona_con_mensajes_juntos_en_un_solo_feed() -> None:
    """Dos pedidos concatenados: `readline()` solo debe consumir el primero."""

    async def _ambos() -> tuple[dict, dict]:
        datos = protocolo.codificar(_pedido(query="status")) + protocolo.codificar(
            _pedido(query="urls")
        )
        reader = _reader_con(datos)
        primero = await protocolo.leer_pedido(reader)
        segundo = await protocolo.leer_pedido(reader)
        return primero, segundo

    primero, segundo = asyncio.run(_ambos())
    assert primero["query"] == "status"
    assert segundo["query"] == "urls"


def test_leer_pedido_funciona_con_el_mensaje_fragmentado_en_varios_feeds() -> None:
    crudo = protocolo.codificar(_pedido())
    mitad = len(crudo) // 2

    async def _alimentar_y_leer() -> dict:
        reader = asyncio.StreamReader(limit=protocolo.LIMITE_PEDIDO)
        reader.feed_data(crudo[:mitad])
        tarea = asyncio.ensure_future(protocolo.leer_pedido(reader))
        await asyncio.sleep(0)
        reader.feed_data(crudo[mitad:])
        return await tarea

    pedido = asyncio.run(_alimentar_y_leer())
    assert pedido["query"] == "status"


def test_leer_pedido_rechaza_json_no_objeto() -> None:
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(b"[1, 2, 3]\n")
    assert exc.value.code == "INVALID_REQUEST"


def test_leer_pedido_rechaza_json_malformado() -> None:
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(b"{no es json}\n")
    assert exc.value.code == "INVALID_REQUEST"


def test_leer_pedido_rechaza_pedido_sin_lf() -> None:
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(protocolo.codificar(_pedido())[:-1])
    assert exc.value.code == "INVALID_REQUEST"


def test_leer_pedido_rechaza_conexion_cerrada_sin_datos() -> None:
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(b"")
    assert exc.value.code == "INVALID_REQUEST"


def test_leer_pedido_rechaza_pedido_demasiado_largo() -> None:
    enorme = _pedido(file="local://" + "/a" * 40000)
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(protocolo.codificar(enorme), limite=1024)
    assert exc.value.code == "INVALID_REQUEST"


def test_leer_pedido_agota_el_timeout_si_no_llega_lf() -> None:
    async def _intentar() -> None:
        reader = asyncio.StreamReader(limit=protocolo.LIMITE_PEDIDO)
        reader.feed_data(b'{"version": 1')  # sin LF ni EOF: nunca llega una línea completa
        await protocolo.leer_pedido(reader, timeout=0.05)

    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        asyncio.run(_intentar())
    assert exc.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("overrides", "codigo_esperado"),
    [
        ({"version": 2}, "INVALID_REQUEST"),
        ({"version": True}, "INVALID_REQUEST"),
        ({"type": "ping"}, "INVALID_REQUEST"),
        ({"extra": "no-declarado"}, "INVALID_REQUEST"),
        ({"top": 0}, "INVALID_REQUEST"),
        ({"top": True}, "INVALID_REQUEST"),
        ({"top": -3}, "INVALID_REQUEST"),
        ({"chunk_mb": 0}, "INVALID_REQUEST"),
        ({"chunk_mb": 257}, "INVALID_REQUEST"),
        ({"chunk_mb": True}, "INVALID_REQUEST"),
        ({"query": "inexistente"}, "UNSUPPORTED_QUERY"),
        ({"file": "ftp://servidor/log"}, "UNSUPPORTED_URI"),
        ({"file": "local:///tmp/access.log.gz"}, "UNSUPPORTED_URI"),
        ({"file": ""}, "INVALID_REQUEST"),
    ],
)
def test_leer_pedido_rechaza_variantes_invalidas(overrides: dict, codigo_esperado: str) -> None:
    pedido = _pedido()
    pedido.update(overrides)
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(protocolo.codificar(pedido))
    assert exc.value.code == codigo_esperado


def test_leer_pedido_rechaza_campo_obligatorio_faltante() -> None:
    pedido = _pedido()
    del pedido["query"]
    with pytest.raises(protocolo.ErrorProtocolo) as exc:
        _leer(protocolo.codificar(pedido))
    assert exc.value.code == "INVALID_REQUEST"


def test_validar_uri_acepta_ruta_local_sin_esquema() -> None:
    protocolo.validar_uri("/datos/access.log")
    protocolo.validar_uri("local:///datos/access.log")
    protocolo.validar_uri("s3://logs/access.log")


def test_mensajes_de_respuesta_tienen_version_y_tipo() -> None:
    assert protocolo.msg_accepted("j1", "queued") == {
        "version": 1,
        "type": "accepted",
        "job_id": "j1",
        "state": "queued",
    }
    assert protocolo.msg_started("j1", 4, 32) == {
        "version": 1,
        "type": "started",
        "job_id": "j1",
        "chunks": 4,
        "chunk_mb": 32,
    }
    assert protocolo.msg_progress("j1", 2, 4) == {
        "version": 1,
        "type": "progress",
        "job_id": "j1",
        "completed": 2,
        "total": 4,
    }
    assert protocolo.msg_error(None, "INVALID_REQUEST", "x") == {
        "version": 1,
        "type": "error",
        "job_id": None,
        "code": "INVALID_REQUEST",
        "message": "x",
    }
    resultado = protocolo.msg_result("j1", lineas=5, invalidas=1)
    assert resultado["type"] == "result"
    assert resultado["lineas"] == 5
    assert resultado["invalidas"] == 1


def test_error_protocolo_rechaza_codigo_desconocido() -> None:
    with pytest.raises(ValueError):
        protocolo.ErrorProtocolo("NO_EXISTE", "mensaje")


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

"""Pruebas del parser combined (issue #3): PROJECT_STATEMENT §7.1 y §7.3."""

from datetime import timedelta, timezone

from loghunter.parser import parsear_linea

_EJEMPLO_VALIDO = (
    b'192.168.1.45 - - [20/Oct/2026:14:32:11 -0300] '
    b'"GET /api/users HTTP/1.1" 200 1534 "-" "curl/8.1"'
)

_EJEMPLO_PROJECT_STATEMENT = [
    b'10.0.0.1 - - [20/Oct/2026:14:00:00 -0300] "GET /a HTTP/1.1" 200 100 "-" "demo"',
    b'10.0.0.2 - - [20/Oct/2026:14:05:00 -0300] "GET /b HTTP/1.1" 404 200 "-" "demo"',
    b'10.0.0.1 - - [20/Oct/2026:14:10:00 -0300] "GET /a HTTP/1.1" 200 50 "-" "demo"',
    b'10.0.0.2 - - [20/Oct/2026:15:00:00 -0300] "GET /a?x=1 HTTP/1.1" 500 - "-" "demo"',
    b'10.0.0.1 - - [20/Oct/2026:15:15:00 -0300] "GET /a HTTP/1.1" 200 300 "-" "demo"',
    b"registro corrupto",
]


def test_ejemplo_normativo_produce_cinco_validos_y_uno_invalido() -> None:
    registros = [parsear_linea(linea) for linea in _EJEMPLO_PROJECT_STATEMENT]
    validos = [registro for registro in registros if registro is not None]
    invalidos = [registro for registro in registros if registro is None]
    assert len(validos) == 5
    assert len(invalidos) == 1


def test_extrae_todos_los_campos_del_ejemplo_del_contrato() -> None:
    registro = parsear_linea(_EJEMPLO_VALIDO)
    assert registro is not None
    assert registro.ip == "192.168.1.45"
    assert registro.metodo == "GET"
    assert registro.destino == "/api/users"
    assert registro.estado == 200
    assert registro.bytes == 1534
    assert registro.timestamp.year == 2026
    assert registro.timestamp.month == 10
    assert registro.timestamp.day == 20
    assert registro.timestamp.hour == 14
    assert registro.timestamp.utcoffset() == timedelta(hours=-3)


def test_acepta_ipv6() -> None:
    linea = (
        b'2001:db8::1 - - [01/Jan/2026:00:00:00 +0000] '
        b'"GET / HTTP/1.1" 200 10 "-" "-"'
    )
    registro = parsear_linea(linea)
    assert registro is not None
    assert registro.ip == "2001:db8::1"


def test_bytes_guion_equivale_a_cero() -> None:
    linea = b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 - "-" "-"'
    registro = parsear_linea(linea)
    assert registro is not None
    assert registro.bytes == 0


def test_acepta_escape_de_comillas_en_campo_citado() -> None:
    linea = (
        b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] '
        b'"GET / HTTP/1.1" 200 10 "ref con \\"comillas\\"" "agente"'
    )
    registro = parsear_linea(linea)
    assert registro is not None
    assert registro.destino == "/"


def test_acepta_crlf_y_ausencia_de_salto_final() -> None:
    assert parsear_linea(_EJEMPLO_VALIDO + b"\r\n") is not None
    assert parsear_linea(_EJEMPLO_VALIDO) is not None


def test_offset_horario_positivo_se_interpreta_correctamente() -> None:
    linea = (
        b'10.0.0.1 - - [01/Jan/2026:10:00:00 +0530] '
        b'"GET / HTTP/1.1" 200 1 "-" "-"'
    )
    registro = parsear_linea(linea)
    assert registro is not None
    assert registro.timestamp.utcoffset() == timedelta(hours=5, minutes=30)
    assert registro.timestamp.astimezone(timezone.utc).hour == 4
    assert registro.timestamp.astimezone(timezone.utc).minute == 30


def test_linea_vacia_es_invalida() -> None:
    assert parsear_linea(b"") is None
    assert parsear_linea(b"\n") is None
    assert parsear_linea(b"\r\n") is None


def test_ip_invalida_rechazada() -> None:
    linea = b'999.999.999.999 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "-"'
    assert parsear_linea(linea) is None


def test_fecha_invalida_rechazada() -> None:
    linea = b'10.0.0.1 - - [32/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "-"'
    assert parsear_linea(linea) is None


def test_mes_desconocido_rechazado() -> None:
    linea = b'10.0.0.1 - - [01/Xxx/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "-"'
    assert parsear_linea(linea) is None


def test_estado_fuera_de_rango_rechazado() -> None:
    linea = b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 999 1 "-" "-"'
    assert parsear_linea(linea) is None


def test_estado_no_numerico_rechazado() -> None:
    linea = b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" ABC 1 "-" "-"'
    assert parsear_linea(linea) is None


def test_bytes_negativos_rechazados() -> None:
    linea = b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 -5 "-" "-"'
    assert parsear_linea(linea) is None


def test_codificacion_utf8_invalida_rechazada() -> None:
    linea = (
        b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] '
        b'"GET /\xff HTTP/1.1" 200 1 "-" "-"'
    )
    assert parsear_linea(linea) is None


def test_campo_citado_no_cerrado_rechazado() -> None:
    linea = b'10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 1 "-" "sin cerrar'
    assert parsear_linea(linea) is None

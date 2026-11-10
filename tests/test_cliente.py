"""Pruebas del cliente CLI (issue #17): PROJECT_STATEMENT §12.1, ADR-0010.

Las funciones puras (normalización, construcción del pedido, consumo de
respuestas) se prueban sin red. Los recorridos de extremo a extremo contra
un coordinador real (IPv4/IPv6, exit codes, `--raw`) corren por subproceso
contra un `Coordinador` con `lanzar_grupo`/`reducir_resultados` fakes, igual
que en test_coordinador.py: no dependen de Celery/Redis reales.
"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import threading
from pathlib import Path

from loghunter import cliente, protocolo

_RAIZ = Path(__file__).resolve().parent.parent


def test_normalizar_archivo_convierte_ruta_relativa_a_local_absoluta(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "access.log").write_bytes(b"x")
    assert cliente._normalizar_archivo("access.log") == f"local://{tmp_path / 'access.log'}"


def test_normalizar_archivo_deja_intacta_una_uri_s3() -> None:
    assert cliente._normalizar_archivo("s3://logs/access.log") == "s3://logs/access.log"


def test_construir_pedido_omite_chunk_mb_si_no_se_especifico() -> None:
    parser = cliente.construir_parser()
    args = parser.parse_args(["-f", "/tmp/a.log", "-q", "status"])
    pedido = cliente._construir_pedido(args)
    assert "chunk_mb" not in pedido
    assert pedido["top"] == 10
    assert pedido["query"] == "status"


def test_construir_pedido_incluye_chunk_mb_explicito() -> None:
    parser = cliente.construir_parser()
    args = parser.parse_args(["-f", "/tmp/a.log", "-q", "status", "--chunk-mb", "64"])
    pedido = cliente._construir_pedido(args)
    assert pedido["chunk_mb"] == 64


def test_parser_rechaza_query_top_y_puerto_invalidos() -> None:
    parser = cliente.construir_parser()
    for argv in (
        ["-f", "/tmp/a.log", "-q", "no-existe"],
        ["-f", "/tmp/a.log", "-q", "status", "--top", "0"],
        ["-f", "/tmp/a.log", "-q", "status", "-p", "70000"],
    ):
        try:
            parser.parse_args(argv)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"se esperaba SystemExit(2) para {argv}")


def _flujo_de(mensajes: list[dict]) -> io.BytesIO:
    crudo = b"".join(protocolo.codificar(m) for m in mensajes)
    return io.BytesIO(crudo)


def test_consumir_respuestas_en_raw_escribe_solo_el_json_terminal_en_stdout(monkeypatch, capsys) -> None:
    mensajes = [
        {"version": 1, "type": "accepted", "job_id": "j1", "state": "admitted"},
        {"version": 1, "type": "started", "job_id": "j1", "chunks": 1, "chunk_mb": 32},
        {"version": 1, "type": "progress", "job_id": "j1", "completed": 1, "total": 1},
        {
            "version": 1, "type": "result", "job_id": "j1", "file": "local:///a.log",
            "query": "status", "top": 10, "chunk_mb": 32, "tamano_bytes": 10, "chunks": 1,
            "workers": 1, "lineas": 1, "invalidas": 0, "lineas_totales": 1, "duracion": 0.1,
            "resultados": [{"clave": "200", "valor": 1}], "persistencia": "no_implementada",
        },
    ]
    flujo = _flujo_de(mensajes)
    codigo = cliente._consumir_respuestas(flujo, raw=True, salida_humana=sys.stderr)
    assert codigo == 0

    capturado = capsys.readouterr()
    assert capturado.out == protocolo.codificar(mensajes[-1]).decode("utf-8")
    assert json.loads(capturado.out) == mensajes[-1]
    assert "admitido" not in capturado.out.lower()


def test_consumir_respuestas_error_termina_con_codigo_uno(capsys) -> None:
    mensajes = [
        {"version": 1, "type": "accepted", "job_id": None, "state": "admitted"},
        {"version": 1, "type": "error", "job_id": "j1", "code": "FILE_NOT_FOUND", "message": "no existe"},
    ]
    flujo = _flujo_de(mensajes)
    codigo = cliente._consumir_respuestas(flujo, raw=False, salida_humana=sys.stdout)
    assert codigo == 1


def test_consumir_respuestas_cierre_sin_terminal_es_fallo() -> None:
    flujo = io.BytesIO(b"")
    codigo = cliente._consumir_respuestas(flujo, raw=False, salida_humana=sys.stdout)
    assert codigo == 1


class _ResultadoFalso:
    def ready(self) -> bool:
        return True

    def successful(self) -> bool:
        return True

    def revoke(self) -> None:
        pass


class _GrupoFalso:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.results = [_ResultadoFalso()]


def _lanzar_grupo_falso(uri, tamano, chunk_bytes, query, job_id):
    return _GrupoFalso(job_id)


def _reducir_resultados_falso(grupo, timeout=None, intervalo=0.2):
    return {"contador": {"200": 2}, "lineas": 2, "invalidas": 0, "workers": 1}


def _lanzar_coordinador_de_prueba(ip: str) -> tuple[int, threading.Thread, threading.Event]:
    """Levanta un Coordinador real (con fakes) en un hilo propio con su loop."""
    from loghunter import coordinador as coordinador_mod

    puerto_listo = threading.Event()
    detener = threading.Event()
    puerto_holder: list[int] = []

    def _correr() -> None:
        async def _servir() -> None:
            coord = coordinador_mod.Coordinador(
                max_jobs=2,
                job_timeout=10.0,
                chunk_mb_default=32,
                lanzar_grupo=_lanzar_grupo_falso,
                reducir_resultados=_reducir_resultados_falso,
                tarea_fallida_exc=RuntimeError,
            )
            servidor = await asyncio.start_server(
                coord.atender_cliente, host=ip, port=0, limit=protocolo.LIMITE_PEDIDO
            )
            puerto_holder.append(servidor.sockets[0].getsockname()[1])
            puerto_listo.set()
            async with servidor:
                await asyncio.get_event_loop().run_in_executor(None, detener.wait)
                servidor.close()

        asyncio.run(_servir())

    hilo = threading.Thread(target=_correr, daemon=True)
    hilo.start()
    puerto_listo.wait(timeout=5)
    return puerto_holder[0], hilo, detener


def _ejecutar_cliente(ip: str, puerto: int, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "loghunter.cliente", "-i", ip, "-p", str(puerto), *extra],
        capture_output=True,
        text=True,
        cwd=str(_RAIZ),
        timeout=15,
    )


def test_cliente_real_exit_code_cero_y_tabla_en_modo_humano(tmp_path: Path) -> None:
    puerto, hilo, detener = _lanzar_coordinador_de_prueba("127.0.0.1")
    try:
        archivo = tmp_path / "a.log"
        archivo.write_bytes(b"x" * 32)
        resultado = _ejecutar_cliente("127.0.0.1", puerto, "-f", str(archivo), "-q", "status")
        assert resultado.returncode == 0
        assert "200" in resultado.stdout
        assert "Duración" in resultado.stdout
    finally:
        detener.set()
        hilo.join(timeout=5)


def test_cliente_real_raw_stdout_es_exactamente_un_json_mas_lf(tmp_path: Path) -> None:
    puerto, hilo, detener = _lanzar_coordinador_de_prueba("127.0.0.1")
    try:
        archivo = tmp_path / "a.log"
        archivo.write_bytes(b"x" * 32)
        resultado = _ejecutar_cliente("127.0.0.1", puerto, "-f", str(archivo), "-q", "status", "--raw")
        assert resultado.returncode == 0
        assert resultado.stdout.endswith("\n")
        assert resultado.stdout.count("\n") == 1
        mensaje = json.loads(resultado.stdout)
        assert mensaje["type"] == "result"
        assert resultado.stderr  # progreso/diagnóstico fueron a stderr, no a stdout
    finally:
        detener.set()
        hilo.join(timeout=5)


def test_cliente_real_archivo_inexistente_sale_con_codigo_uno(tmp_path: Path) -> None:
    puerto, hilo, detener = _lanzar_coordinador_de_prueba("127.0.0.1")
    try:
        resultado = _ejecutar_cliente(
            "127.0.0.1", puerto, "-f", str(tmp_path / "no-existe.log"), "-q", "status"
        )
        assert resultado.returncode == 1
        assert "FILE_NOT_FOUND" in resultado.stderr
    finally:
        detener.set()
        hilo.join(timeout=5)


def test_cliente_real_funciona_por_ipv6(tmp_path: Path) -> None:
    try:
        puerto, hilo, detener = _lanzar_coordinador_de_prueba("::1")
    except OSError:
        return  # IPv6 no disponible en este entorno de CI/host
    try:
        archivo = tmp_path / "a.log"
        archivo.write_bytes(b"x" * 32)
        resultado = _ejecutar_cliente("::1", puerto, "-f", str(archivo), "-q", "status")
        assert resultado.returncode == 0
        assert "200" in resultado.stdout
    finally:
        detener.set()
        hilo.join(timeout=5)


def test_cliente_real_argumentos_invalidos_sale_con_codigo_dos() -> None:
    resultado = subprocess.run(
        [sys.executable, "-m", "loghunter.cliente", "-f", "/tmp/a.log", "-q", "no-existe"],
        capture_output=True,
        text=True,
        cwd=str(_RAIZ),
        timeout=15,
    )
    assert resultado.returncode == 2
    assert resultado.stdout == ""


if __name__ == "__main__":
    raise SystemExit("Ejecutar con pytest.")

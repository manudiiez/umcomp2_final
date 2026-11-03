"""Contrato de instalación e importación independiente del directorio del repo."""

import subprocess
import sys
from pathlib import Path


def test_installed_modules_import_without_starting_services(tmp_path: Path) -> None:
    """Importar el paquete instalado no conecta, inicia procesos ni abre SQLite."""
    script = """
import importlib
import multiprocessing.process
import socket
import sqlite3
import subprocess
import threading

def forbidden(*args, **kwargs):
    raise AssertionError("La importación intentó iniciar un servicio o una conexión")

socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.bind = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
multiprocessing.process.BaseProcess.start = forbidden
threading.Thread.start = forbidden

for name in (
    "loghunter",
    "loghunter.cliente",
    "loghunter.coordinador",
    "loghunter.tasks",
    "loghunter.lector",
    "loghunter.indexador",
    "loghunter.generar",
):
    importlib.import_module(name)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []

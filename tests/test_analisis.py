"""Pruebas de la correlación de grupos y el reduce distribuido (issue #13).

PROJECT_STATEMENT §§9.2, 9.3; ADR-0007. Usa un doble de `AsyncResult`: no
requiere Redis ni un worker real. El recorrido de extremo a extremo
(publicar de verdad, perder un worker, revocar) se verifica con
infraestructura real en el issue #14.
"""

from __future__ import annotations

import pytest

from loghunter.analisis import Grupo, TareaFallida, lanzar_grupo, nuevo_job_id, reducir_resultados


class _ResultadoFalso:
    """Doble de `celery.result.AsyncResult`: solo lo que usa `reducir_resultados`."""

    def __init__(self, id: str, resultado: dict | None = None, fallo: Exception | None = None) -> None:
        self.id = id
        self._resultado = resultado
        self._fallo = fallo
        self.revocado = False

    def ready(self) -> bool:
        return self._resultado is not None or self._fallo is not None

    def failed(self) -> bool:
        return self._fallo is not None

    @property
    def result(self):
        return self._fallo if self._fallo is not None else self._resultado

    def revoke(self) -> None:
        self.revocado = True


class _ResultadoQueNuncaTermina(_ResultadoFalso):
    def __init__(self, id: str) -> None:
        super().__init__(id)

    def ready(self) -> bool:
        return False


def _parcial(contador: dict, lineas: int, invalidas: int, hostname: str, pid: int) -> dict:
    return {
        "contador": contador,
        "lineas": lineas,
        "invalidas": invalidas,
        "worker": {"hostname": hostname, "pid": pid},
    }


# --- reducir_resultados ---------------------------------------------------


def test_reduce_todas_exitosas_suma_contadores_lineas_e_invalidas() -> None:
    grupo = Grupo(
        id="g",
        results=[
            _ResultadoFalso("t1", _parcial({"10.0.0.1": 2}, 2, 0, "w1", 100)),
            _ResultadoFalso("t2", _parcial({"10.0.0.1": 1, "10.0.0.2": 3}, 4, 1, "w1", 100)),
            _ResultadoFalso("t3", _parcial({"10.0.0.2": 1}, 1, 0, "w2", 200)),
        ]
    )

    resultado = reducir_resultados(grupo)

    assert resultado["contador"] == {"10.0.0.1": 3, "10.0.0.2": 4}
    assert resultado["lineas"] == 7
    assert resultado["invalidas"] == 1
    assert resultado["workers"] == 2  # (w1,100) y (w2,200): procesos observados


def test_grupo_sin_resultados_devuelve_agregado_vacio_sin_iterar() -> None:
    assert reducir_resultados(Grupo(id="g", results=[])) == {
        "contador": {},
        "lineas": 0,
        "invalidas": 0,
        "workers": 0,
    }


def test_fallo_definitivo_levanta_tareafallida_sin_esperar_el_resto() -> None:
    grupo = Grupo(
        id="g",
        results=[
            _ResultadoFalso("ok", _parcial({"a": 1}, 1, 0, "w1", 1)),
            _ResultadoFalso("falla", fallo=RuntimeError("boom")),
            _ResultadoQueNuncaTermina("pendiente"),  # nunca ready(): no debe bloquear
        ]
    )

    with pytest.raises(TareaFallida):
        reducir_resultados(grupo)


def test_timeout_vencido_levanta_timeout_error() -> None:
    grupo = Grupo(id="g", results=[_ResultadoQueNuncaTermina("pendiente")])

    with pytest.raises(TimeoutError):
        reducir_resultados(grupo, timeout=0.05, intervalo=0.01)


def test_cada_task_id_aporta_una_sola_vez() -> None:
    grupo = Grupo(
        id="g",
        results=[
            _ResultadoFalso("t1", _parcial({"a": 1}, 1, 0, "w1", 1)),
            _ResultadoFalso("t2", _parcial({"a": 1}, 1, 0, "w1", 1)),
        ]
    )
    resultado = reducir_resultados(grupo)
    assert resultado["contador"] == {"a": 2}
    assert resultado["lineas"] == 2


# --- lanzar_grupo ----------------------------------------------------------


def test_archivo_vacio_no_publica_ninguna_tarea(monkeypatch: pytest.MonkeyPatch) -> None:
    import loghunter.analisis as analisis_mod

    def _no_debe_llamarse(*args, **kwargs):
        raise AssertionError("no debe publicar tareas para un archivo vacío")

    monkeypatch.setattr(analisis_mod.map_chunk, "apply_async", _no_debe_llamarse)

    grupo = lanzar_grupo("local:///a.log", 0, 32 * 2**20, "status", nuevo_job_id())
    assert grupo.results == []


def test_pregenera_task_ids_distintos_antes_de_publicar(monkeypatch: pytest.MonkeyPatch) -> None:
    import loghunter.analisis as analisis_mod

    task_ids_usados: list[str] = []

    def _fake_apply_async(args, task_id):
        task_ids_usados.append(task_id)
        return _ResultadoFalso(task_id, _parcial({}, 0, 0, "w1", 1))

    monkeypatch.setattr(analisis_mod.map_chunk, "apply_async", _fake_apply_async)

    tamano = 100 * 2**20  # 4 tramos de 32 MiB
    grupo = lanzar_grupo("local:///a.log", tamano, 32 * 2**20, "status", nuevo_job_id())

    assert len(grupo.results) == 4
    assert len(set(task_ids_usados)) == 4  # todos distintos


def test_publicacion_parcial_fallida_revoca_lo_ya_publicado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loghunter.analisis as analisis_mod

    publicados: list[_ResultadoFalso] = []

    def _fake_apply_async(args, task_id):
        if len(publicados) == 2:
            raise ConnectionError("broker caído a mitad de la publicación")
        resultado = _ResultadoFalso(task_id, _parcial({}, 0, 0, "w1", 1))
        publicados.append(resultado)
        return resultado

    monkeypatch.setattr(analisis_mod.map_chunk, "apply_async", _fake_apply_async)

    tamano = 128 * 2**20  # 4 tramos de 32 MiB; falla al publicar el 3ro
    with pytest.raises(ConnectionError):
        lanzar_grupo("local:///a.log", tamano, 32 * 2**20, "status", nuevo_job_id())

    assert len(publicados) == 2
    assert all(resultado.revocado for resultado in publicados)

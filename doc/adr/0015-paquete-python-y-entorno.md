# ADR-0015 — Paquete instalable, entorno virtual y dependencias reproducibles

- Fecha: 2026-11-03.
- Estado: adoptado para la implementación del issue #2.

## Contexto

El proyecto necesita una base instalable con CPython 3.12, pruebas ejecutables
desde un checkout limpio y responsabilidades separadas. La instalación debe
aislarse de otros proyectos y no depender de importar archivos desde el cwd.

## Decisión

Usar `pyproject.toml`, layout `src/loghunter` y setuptools como backend PEP 517,
fijado en 80.9.0. La familia del intérprete es 3.12: `.python-version` la selecciona
y `requires-python = ">=3.12,<3.13"` la declara para la instalación.

Administrar el entorno local `.venv` mediante `uv`, con dependencias de desarrollo
en el grupo `dev` y versiones resueltas en `uv.lock`. Usar `uv sync --locked` y
`uv run --locked python -m pytest` para recrear y verificar el entorno. No versionar
el entorno, cachés ni distribuciones generadas. El lockfile no fija el parche de
CPython ni sustituye mantener el intérprete actualizado.

Mantener el import del paquete y de los módulos libre de inicialización de
servicios. Reservar los módulos previstos con su responsabilidad e issue; mientras
no estén implementados, ejecutarlos directamente falla de forma explícita. No
publicar comandos vacíos que terminen exitosamente ni crear entrypoints nuevos
fuera de la interfaz `python -m loghunter.<modulo>` acordada.

Introducir solo pytest como dependencia de desarrollo en esta etapa. Las librerías
de producción se agregan junto con las funcionalidades que las necesitan. Las
pruebas usan importlib, sin añadir `src` a PYTHONPATH; una prueba ejecuta imports
en un proceso aislado desde otro directorio y bloquea conexiones e inicio de servicios.

## Alternativas

- Paquete en la raíz con imports desde el cwd: puede ocultar que la distribución
  no se instaló o que faltan archivos del paquete.
- Dependencias instaladas globalmente: mezclan entornos y dificultan reproducir fallos.
- Rangos de dependencias sin lockfile: no fijan las versiones de una instalación.
- Instalar desde el inicio toda la infraestructura futura: agrega dependencias
  aún sin uso y no demuestra su integración.

## Consecuencias

El desarrollo requiere instalar primero el paquete. Las versiones elegidas de
dependencias quedan en el lockfile y los cambios se revisan junto a pyproject.
Se necesita uv para el flujo de desarrollo documentado; el paquete construido
se puede instalar con herramientas estándar de Python.

La base todavía no analiza logs ni levanta servidores. Las verificaciones de
instalación, importación y empaquetado son el resultado de esta etapa.

## Verificación y referencias

Issue #2 y PROJECT_STATEMENT §§4 y 6. Verificar instalación editable, importación
sin servicios, construcción de wheel/sdist e instalación de la wheel fuera del repo.
Referencias técnicas: [empaquetado de Python](https://packaging.python.org/en/latest/tutorials/packaging-projects/),
[entorno y lockfile de uv](https://docs.astral.sh/uv/concepts/projects/layout/),
[sincronización bloqueada](https://docs.astral.sh/uv/concepts/projects/sync/) y
[prácticas de pytest](https://docs.pytest.org/en/stable/explanation/goodpractices.html).

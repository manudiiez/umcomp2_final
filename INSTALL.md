# Instalación y entorno de desarrollo

## Estado actual

El issue #2 prepara el paquete instalable y las pruebas iniciales. Los módulos
de cliente, coordinador, workers, lector, indexador y generador tienen su ubicación
reservada. Su ejecución informa que todavía no están implementados y termina
con error; no inicia servicios ni simula un análisis exitoso.

Los requisitos y futuras funcionalidades están en
[PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md), y el orden de implementación en
[DEVELOPMENT_PLAN.md](doc/DEVELOPMENT_PLAN.md).

## Requisitos

- CPython 3.12 disponible como `python3.12` o seleccionable por `uv`.
- Git y [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.8.0 o superior.
- Acceso a PyPI durante la primera instalación, salvo que los paquetes estén en caché.

`uv` administra las dependencias y crea el entorno virtual local `.venv`; no es
una dependencia de ejecución de LogHunter. `.python-version` selecciona la familia
3.12 y `requires-python` impide instalar el proyecto con otra versión menor.
Utilizar una versión de mantenimiento actualizada de CPython 3.12.

## Preparar el entorno

```bash
git clone https://github.com/manudiiez/umcomp2_final.git
cd umcomp2_final
# Hasta integrar esta etapa en main, seleccionar la rama que contiene este issue.
git switch build/2-estructura-proyecto
uv sync --locked --python 3.12
```

Al estar el cambio integrado en otra rama, seleccionar esa rama en lugar de la
del ejemplo. El comando instala el proyecto en modo editable y las herramientas
del grupo `dev`, con las versiones exactas de `uv.lock`. El entorno `.venv/` es
local y no se versiona. Para usar un intérprete concreto:

```bash
uv sync --locked --python /ruta/al/python3.12
```

El Python base debe estar instalado; su actualización se hace fuera del entorno
virtual. El lockfile fija las dependencias de Python, no el parche del intérprete.
La instalación utiliza el índice público de PyPI; no requiere credenciales del repo.

## Comprobar la instalación y ejecutar pruebas

```bash
uv run --locked python -c "import loghunter; print(loghunter.__file__)"
uv run --locked python -m pytest
uv sync --locked --check
```

`pytest` comprueba que el paquete instalado y sus módulos se importan desde otro
directorio con el modo aislado de Python, sin conexiones, procesos, hilos o bases
de datos iniciados durante el import. No se configura `PYTHONPATH` para encontrar
accidentalmente el código fuente sin instalarlo.

La activación es opcional cuando se usa `uv run`. Para trabajar con el Python
del entorno directamente, en Linux/macOS:

```bash
source .venv/bin/activate
python --version
python -m pytest
deactivate
```

No ejecutar `uv sync` desde el entorno activado de otro proyecto: cada checkout
mantiene su propia `.venv`.

## Dependencias y distribución

`pyproject.toml` define el paquete, el backend de construcción y la dependencia
de desarrollo `pytest`. `uv.lock` registra las versiones resueltas y hashes; ambos
archivos se versionan juntos. No editar el lockfile a mano.

En esta etapa LogHunter usa solo la biblioteca estándar. Celery, Redis y boto3
se incorporarán al implementar sus módulos, agregando la dependencia a
`pyproject.toml` y actualizando el lockfile en la rama de su issue.

Para construir la distribución local, sin publicarla en PyPI:

```bash
uv build
```

Los archivos generados quedan en `dist/`, que no se versiona. El backend de
construcción es setuptools 80.9.0, fijado en `pyproject.toml`; sus requisitos de
construcción se resuelven en un entorno aislado.

## Servicios externos

La instalación y las pruebas iniciales no necesitan Redis, MinIO ni Docker.
Su arranque, configuración y pruebas se documentarán al implementar los issues
de infraestructura y despliegue.

# Instalación y entorno de desarrollo

## Estado actual

El milestone 2 implementó el núcleo de análisis local: `loghunter.parser`,
`loghunter.consultas`, `loghunter.generar`, `loghunter.lector` (backend
`local://`) y `loghunter.workers` (map-reduce por tramos en memoria), con
pruebas que comparan el resultado por tramos contra una referencia
secuencial (ver [VERIFICACION_NUCLEO.md](doc/VERIFICACION_NUCLEO.md)).

El milestone 3 agregó la distribución remota: `loghunter.tasks` (tarea
Celery `map_chunk` sobre Redis), el backend `s3://` de `loghunter.lector` y
la subida de logs a MinIO en `loghunter.generar`, y `loghunter.analisis`
(correlación de grupos de tareas y reduce de parciales). Verificado con
Redis, MinIO y workers Celery reales, incluyendo un worker sin acceso al
filesystem del log y la recuperación tras perder un worker (ver
[VERIFICACION_DISTRIBUCION.md](doc/VERIFICACION_DISTRIBUCION.md)).

`cliente.py`, `coordinador.py` e `indexador.py` siguen con su ubicación
reservada: su ejecución informa que todavía no están implementados y
termina con error; no inician servicios ni simulan un análisis exitoso.

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

A partir del issue #9, `pyproject.toml` agrega `celery[redis]` y `boto3` como
dependencias de ejecución. Las pruebas del backend S3 (issue #11) no
dependen de Docker ni de un MinIO real: inyectan un cliente `boto3` doble
propio (`tests/test_lector_s3.py`), porque `moto` —la alternativa obvia para
simular S3 en memoria— no intercepta correctamente las llamadas con la
versión de `botocore` instalada en este entorno; se evaluó y se descartó
por eso, no por preferencia de estilo. La verificación real contra Redis y
MinIO en Docker (issues #10, #13 y #14) se documenta por separado y no es
parte de la suite automática de esta instalación.

Para construir la distribución local, sin publicarla en PyPI:

```bash
uv build
```

Los archivos generados quedan en `dist/`, que no se versiona. El backend de
construcción es setuptools 80.9.0, fijado en `pyproject.toml`; sus requisitos de
construcción se resuelven en un entorno aislado.

## Servicios externos

La instalación y la mayoría de las pruebas (incluidas las del backend S3, que
usan `moto` para simular MinIO en memoria) no necesitan Redis, MinIO ni Docker.
Son necesarios solo para la ejecución real de tareas distribuidas (issues
#10, #13 y #14) y para `python -m loghunter.generar -o s3://...` contra un
MinIO real. Este milestone **no** instala Docker Compose todavía (eso es el
issue #24): los siguientes comandos arrancan cada servicio por separado, para
desarrollo en un solo host.

### Redis (broker y backend de resultados)

```bash
docker run -d --name loghunter-redis -p 6379:6379 redis:7-alpine
```

Verificación:

```bash
docker exec loghunter-redis redis-cli ping   # PONG
```

### MinIO (almacenamiento compatible con S3)

Las imágenes `minio/minio` dejaron de estar disponibles sin autenticación en
Docker Hub y `quay.io/minio/minio` exige login desde la migración de MinIO a
una licencia más restrictiva (2024-2025); no es un cambio de arquitectura
(ADR-0004 sigue vigente: sigue siendo la API S3 de MinIO), solo de origen de
la imagen. Se usa el espejo `bitnamilegacy/minio`, con tag fijo, verificado al
implementar este issue:

```bash
docker run -d --name loghunter-minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  bitnamilegacy/minio:2025.7.23-debian-12-r5
```

Verificación:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9000/minio/health/live   # 200
```

Credenciales: `minioadmin` / `minioadmin` son solo para desarrollo local; no
usar estos valores fuera del laboratorio. La consola de administración queda
en `http://localhost:9001` con las mismas credenciales.

### Crear el bucket de pruebas

El operador crea el bucket antes de subir o leer nada; ni `generar.py` ni
`lector.py` lo crean automáticamente. Con el cliente de `boto3` ya instalado
en el entorno del proyecto:

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python -c "
import boto3
from botocore.config import Config
cliente = boto3.client('s3', endpoint_url='http://localhost:9000',
                        config=Config(s3={'addressing_style': 'path'}))
cliente.create_bucket(Bucket='logs')
print(cliente.list_buckets()['Buckets'])
"
```

### Variables de entorno compartidas

Ver PROJECT_STATEMENT §12.4. Flags explícitos de cada comando prevalecen sobre
estas variables, y estas sobre los defaults del código:

| Variable | Default usado por LogHunter | Uso |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Broker de Celery (coordinador y workers) |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | Backend de resultados de Celery |
| `S3_ENDPOINT` | `http://localhost:9000` | Endpoint S3 de MinIO |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | sin default: deben definirse | Credenciales de `boto3`; nunca se versionan ni se imprimen en logs |
| `AWS_DEFAULT_REGION` | `us-east-1` | Región exigida por la API S3, sin significado real en MinIO |
| `LOGHUNTER_TASK_TIME_LIMIT` | `120` (segundos) | Límite duro de una tarea Celery |
| `LOGHUNTER_VISIBILITY_TIMEOUT` | `180` (segundos) | `visibility_timeout` del broker Redis |
| `LOGHUNTER_RESULT_EXPIRES` | `3600` (segundos) | Retención de resultados en el backend |

### Limpiar

```bash
docker rm -f loghunter-redis loghunter-minio
```

Sin volumen montado, los datos de MinIO se pierden al eliminar el contenedor;
eso es aceptable para desarrollo. El despliegue del issue #24 sí define
volúmenes persistentes (PROJECT_STATEMENT §14).

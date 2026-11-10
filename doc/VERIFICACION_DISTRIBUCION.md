# Verificación de la distribución remota (milestone 3)

Evidencia de avance parcial para el issue #14: el análisis se distribuye de
verdad con Celery/Redis, lee de verdad desde MinIO por `GET Range`, y un
worker sin acceso al filesystem del log (en un contenedor separado) procesa
tramos correctamente. También se verificó la recuperación tras matar un
worker a mitad de una tarea. No es la aplicación cliente-servidor completa
(coordinador `asyncio`, protocolo TCP): eso es la milestone 4.

## Qué queda demostrado

- Backend `local://` distribuido con Celery (1-4 MiB de tramo, las cuatro
  consultas) coincide exactamente con `consultas.calcular_referencia`.
- Backend `s3://` contra MinIO real coincide exactamente con la misma
  referencia y con el backend local para el mismo log (AC-02).
- Un worker en un **contenedor Docker separado**, sin ningún archivo montado
  y sin poder resolver ninguna ruta local, procesa tramos `s3://` igual que
  el worker local, con resultado agregado correcto (AC-08, dentro del límite
  de infraestructura explicado abajo).
- Matar (`kill -9`) el worker local a mitad de un job grande no pierde ni
  duplica registros: las tareas en vuelo se redistribuyen a otro worker
  dentro del `visibility_timeout` configurado (AC-07, AC-09).
- Un worker remoto que **no** comparte filesystem con el log falla
  correctamente (no silenciosamente) cuando le toca un tramo `local://`: es
  el comportamiento esperado de CU-06/PROJECT_STATEMENT §8.3, no un defecto
  de `analisis.py`.

## Límite de infraestructura reconocido

No hay una segunda máquina física disponible para esta verificación. La
separación cómputo/almacenamiento se demuestra con un **contenedor Docker
aparte** para el worker "remoto": no monta el archivo de log, solo alcanza
Redis y MinIO por la red del contenedor, y expone un hostname/PID distintos
del proceso local. Es la evidencia disponible en este entorno de laboratorio,
no una sustitución de una demo entre hosts reales (issue #14 lo permite
explícitamente: "las verificaciones... registran límites de
infraestructura").

## Cómo reproducirlo

Infraestructura (ver INSTALL.md para el detalle):

```bash
docker network create loghunter-net
docker run -d --name loghunter-redis --network loghunter-net --network-alias redis \
  -p 6379:6379 redis:7-alpine
docker run -d --name loghunter-minio --network loghunter-net --network-alias minio \
  -p 9000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  bitnamilegacy/minio:2025.7.23-debian-12-r5

# bucket de pruebas
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python -c "
import boto3
from botocore.config import Config
boto3.client('s3', endpoint_url='http://localhost:9000',
              config=Config(s3={'addressing_style': 'path'})).create_bucket(Bucket='logs-it')
"

# worker local (host)
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
S3_ENDPOINT=http://localhost:9000 \
uv run --locked celery -A loghunter.tasks worker --loglevel=INFO -c 1 -n local@%h
```

Pruebas automáticas (se saltan solas si no hay infraestructura arriba):

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
S3_ENDPOINT=http://localhost:9000 \
uv run --locked python -m pytest -q tests/test_integracion_distribucion.py
```

Resultado real (2026-11-10): **11 passed** con Redis, MinIO y un worker local
reales arriba; **11 skipped**, con motivo explícito, cuando se apagan.

## Worker en otro host (contenedor separado)

```bash
cat > Dockerfile.worker-remoto << 'EOF'
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir "celery[redis]>=5.4,<6" "boto3>=1.34,<2" \
    && pip install --no-cache-dir -e .
CMD ["celery", "-A", "loghunter.tasks", "worker", "--loglevel=INFO", "-c", "1", "-n", "remoto@%h"]
EOF
docker build -t loghunter-worker-remoto:verificacion -f Dockerfile.worker-remoto .
docker run -d --name loghunter-worker-remoto --network loghunter-net \
  -e CELERY_BROKER_URL=redis://redis:6379/0 -e CELERY_RESULT_BACKEND=redis://redis:6379/1 \
  -e S3_ENDPOINT=http://minio:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin -e AWS_DEFAULT_REGION=us-east-1 \
  loghunter-worker-remoto:verificacion
```

Confirmado que el contenedor no tiene ningún archivo de log accesible:

```bash
docker exec loghunter-worker-remoto sh -c "find / -xdev -iname '*.log' 2>/dev/null"
# -> solo logs de apt/dpkg del sistema; ninguno del proyecto
```

Un log de 8 MiB (semilla fija) generado a la vez en local y en
`s3://logs-it/access.log` produce exactamente los mismos totales
(`validas=76096, invalidas=1613`). Con el worker local y el worker en el
contenedor separado arriba a la vez, un análisis `status` sobre
`s3://logs-it/access.log` con tramos de 512 KiB (17 tareas) dio, real:

```text
tareas publicadas: 17
hostnames/pid distintos que ejecutaron tareas: {('6495904113d2', 8), ('MacBook-Pro-de-Manuel.local', 15505)}
agregado distribuido (lineas, invalidas, workers): 76096 1613 2
referencia secuencial (lineas, invalidas): 76096 1613
COINCIDE: agregado vía s3:// con worker remoto == referencia secuencial
```

`6495904113d2` es el hostname del contenedor (su propio ID, distinto del
host); ambos workers aportaron tareas y el agregado coincide con la
referencia secuencial.

**Nota real encontrada al verificar:** con ambos workers escuchando la misma
cola, un tramo `local://` que cae en el worker del contenedor falla con
`FileNotFoundError` (no tiene el archivo) — correcto según CU-06/§8.3, pero
significa que las pruebas de backend `local://` de
`tests/test_integracion_distribucion.py` asumen que *todos* los workers
conectados comparten el filesystem. Por eso ese worker remoto se detiene
antes de correr esa parte de la suite; queda documentado en el docstring del
archivo de pruebas.

## Pérdida de un worker a mitad de una tarea

Con `LOGHUNTER_VISIBILITY_TIMEOUT=10` (reducido solo para esta demostración;
el default de producción sigue siendo 180s), un log de 30 MiB subido a
`s3://logs-it/grande.log`, tramos de 64 KiB (481 tareas) y dos workers
activos (local `-c 1` y el del contenedor `-c 1`):

1. Se lanza el grupo (`analisis.lanzar_grupo`) y se empiezan a resolver
   tareas con ambos workers.
2. A los pocos segundos, con cientos de tareas todavía pendientes, se mata
   el proceso del worker local con `kill -9` (incluye su hijo `prefork`).
3. El progreso se enlentece (solo queda el worker del contenedor) pero
   **no se detiene**: las tareas que tenía en vuelo el worker muerto se
   redistribuyen después del `visibility_timeout`.
4. Se reinicia el worker local (ahora con `-c 2`) para no esperar varios
   minutos; esto no invalida la recuperación ya observada en el paso 3, solo
   agrega capacidad.

Salida real completa:

```text
tareas publicadas: 481 en t=0.42s
...
t=199.5s pendientes=0
terminado en t=199.52s
hostnames/pid que ejecutaron tareas: {('MacBook-Pro-de-Manuel.local', 23222), ('ca9a5da3ee05', 8), ('MacBook-Pro-de-Manuel.local', 19989), ('MacBook-Pro-de-Manuel.local', 23223)}
agregado (lineas, invalidas, workers): 285607 5867 4
referencia (lineas, invalidas): 285607 5867
COINCIDE: sin duplicar ni perder registros tras la perdida del worker local
```

El PID `19989` es el worker local **matado** a mitad de camino: aparece en
el conjunto de procesos que efectivamente aportaron resultados porque
procesó varias tareas antes de morir; las que tenía pendientes en ese
momento fueron redelivered y las terminaron `23222`/`23223` (el worker local
reiniciado) o `ca9a5da3ee05` (el contenedor, que nunca se cayó). El agregado
final coincide exactamente con la referencia secuencial: ni se perdió ni se
contó dos veces ningún tramo.

## Límites de esta verificación

- Un solo host físico ejecuta todo (Docker simula la separación de host del
  worker remoto, como se explica arriba); no hay una segunda máquina real.
- `LOGHUNTER_VISIBILITY_TIMEOUT` se redujo a 10s solo para que la
  demostración de pérdida de worker no tome varios minutos; el valor por
  defecto documentado (180s) no cambia.
- No se mide rendimiento ni se compara 1 contra 2 contra 4 procesos; eso es
  el benchmark del issue #26.
- La capa de coordinador/cliente/TCP todavía no existe (milestone 4):
  `analisis.lanzar_grupo`/`reducir_resultados` se invocan directamente desde
  los scripts de verificación, no desde un servidor real.

## Referencias

PROJECT_STATEMENT §§8.3, 9.2, 9.3 y 12.4; CU-06, CU-07, CU-13, CU-20;
AC-02, AC-07, AC-08, AC-09, AC-16; ADR-0003, ADR-0004, ADR-0007, ADR-0012.

# Resumen del milestone 3: distribución y almacenamiento remoto

Issue #42. Documenta, en un solo lugar, qué se hizo en los issues #9 a #14 y
en qué archivo vivió cada parte. Para los comandos de verificación y sus
resultados reales, ver [VERIFICACION_DISTRIBUCION.md](../VERIFICACION_DISTRIBUCION.md).

## Resumen

Esta etapa distribuyó el map-reduce del milestone 2 con **Celery y Redis**,
agregó lectura y escritura de logs en **MinIO** (API S3) por `GET Range`, y
una capa de correlación de grupos de tareas y reduce de parciales
reutilizable por el futuro coordinador. Se verificó con infraestructura
real (no simulada): un worker en un contenedor sin acceso al filesystem del
log procesando tramos `s3://`, y la recuperación de un job tras matar un
worker a mitad de una tarea, ambos con el agregado final coincidiendo
exactamente con la referencia secuencial del milestone 2.

Issues integrados: #9 (infraestructura), #10 (tareas Celery), #11 (lector
S3), #12 (generador a MinIO), #13 (correlación y reduce), #14
(verificación), mergeados en `milestone/3-distribucion-remota`.

## Por archivo / directorio

### `pyproject.toml` / `INSTALL.md` (#9)

Agrega `celery[redis]` y `boto3` como dependencias de ejecución. Documenta
el arranque de Redis y MinIO para desarrollo con `docker run`, la creación
del bucket de pruebas y las variables de entorno compartidas (broker,
backend, endpoint S3, credenciales, límites de tarea/visibilidad/retención).
Las imágenes `minio/minio` y `quay.io/minio/minio` dejaron de descargarse
sin autenticación por un cambio de licenciamiento de MinIO (no de
arquitectura); se fijó `bitnamilegacy/minio:2025.7.23-debian-12-r5` como
alternativa verificada.

### `src/loghunter/tasks.py` (#10)

App Celery configurada por entorno: confirmación tardía
(`task_acks_late`), rechazo al perder el ejecutor
(`task_reject_on_worker_lost`), límite duro de tarea, `visibility_timeout`
y retención de resultados (PROJECT_STATEMENT §9.3). La tarea `map_chunk`
reutiliza `workers.map_chunk` **sin cambios** y solo adapta su salida al
contrato de red (`contador`, `lineas`, `invalidas`, `worker`). No reintenta
nada por sí misma: un error permanente propaga como `FAILURE`; la
recuperación ante pérdida del ejecutor depende del broker.

### `src/loghunter/lector.py` (#11)

Agrega `LectorS3`, con el mismo contrato que `LectorLocal`: `HEAD` para el
tamaño, `GET Range` para leer, reintentos propios finitos con backoff solo
para fallos transitorios, y errores permanentes (objeto/bucket ausente,
acceso denegado) que se distinguen y propagan de inmediato en vez de
reintentarse. `leer_hasta_salto` se factorizó como función compartida entre
ambos backends. Se intentó usar `moto` para simular MinIO en los tests sin
Docker, pero su versión disponible no intercepta correctamente las
llamadas con la versión de `botocore` instalada aquí; se descartó esa
dependencia y las pruebas inyectan un cliente S3 doble propio.

### `src/loghunter/generar.py` (#12)

`escribir_log(destino="s3://bucket/key")` sube con `upload_fileobj` a
través de `_FlujoGenerado`, un adaptador `.read(size)` que nunca junta el
archivo completo en memoria y cuenta válidas/inválidas en la misma pasada
que sube los bytes. No crea el bucket si falta: reporta un error claro, sin
anunciar éxito. Verificado contra MinIO real: misma semilla/argumentos
producen exactamente los mismos bytes en `s3://` y en local.

### `src/loghunter/analisis.py` (#13, nuevo)

La capa que el futuro coordinador (milestone 4) va a envolver con
`asyncio.to_thread`: `lanzar_grupo()` pre-genera un `task_id` por rango
antes de publicar nada (y revoca lo ya publicado si una publicación falla a
mitad de camino) y `reducir_resultados()` distingue éxito/fallo por tarea
en vez de confiar solo en `ready()` del grupo, levantando `TareaFallida` sin
esperar el 100% de éxitos. No usa `celery.result.GroupResult` (exige que
cada resultado implemente el protocolo de promesas `.then()`, innecesario
porque el sondeo es propio): `Grupo` es una clase simple propia.

### `tests/` (#10-#14)

`test_tasks.py`, `test_lector_s3.py` y `test_analisis.py` usan dobles de
prueba (ningún Redis/MinIO real necesario); `test_generar.py` se amplió en
la misma línea para la subida a S3. `test_integracion_distribucion.py`
(#14) es la excepción: requiere Redis, MinIO y un worker Celery reales, y
se salta sola con un motivo explícito si no están arriba.

### `doc/VERIFICACION_DISTRIBUCION.md` (#14)

Documento nuevo, con salida real (no hipotética): equivalencia local/S3 por
tramos, un worker en un contenedor Docker separado sin ningún archivo
montado (confirmado con `find`) procesando tramos `s3://` junto al worker
local, y la pérdida de un worker a mitad de un job de 481 tareas,
recuperado por el otro worker sin duplicar ni perder líneas. Documenta
también un hallazgo real: un worker sin el filesystem del log falla
correctamente ante un tramo `local://` (CU-06/§8.3), por lo que las pruebas
automáticas de ese backend asumen que todos los workers conectados
comparten filesystem.

### `doc/milestone/` (#42, esta carpeta)

Nueva carpeta para los resúmenes de cada etapa: este archivo y
[RESUMEN_MILESTONE_2.md](RESUMEN_MILESTONE_2.md) (movido desde `doc/`).

### `README.md` / `INSTALL.md`

Actualizados en la sección "Estado": la distribución remota ya está
implementada y verificada; listan qué sigue pendiente (cliente, coordinador
e indexador, de los milestones 4 y 5).

## Qué NO incluye esta etapa

Protocolo cliente↔coordinador y servidor `asyncio` (#15-#19), persistencia
e IPC (#20-#22), Docker Compose de despliegue y entrega final (#23-#28). El
Docker usado en esta etapa es solo para desarrollo/verificación (contenedores
sueltos con `docker run`, no el Compose final del issue #24). `analisis.py`
se invoca directamente desde scripts/pruebas, no desde un servidor real: eso
es la milestone 4.

## Validación

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest -q
```

**131 pruebas, 0 fallos, 11 saltadas** (requieren Redis/MinIO/worker reales)
sin infraestructura levantada. Con Redis, MinIO y un worker Celery reales
arriba (ver INSTALL.md):

```bash
uv run --locked python -m pytest -q
```

**142 pruebas, 0 fallos**, sobre la rama `milestone/3-distribucion-remota`
ya integrada con los 6 issues.

## Referencias

PROJECT_STATEMENT §§4, 8.3, 9, 12.3 y 12.4; CU-06, CU-07, CU-08, CU-13,
CU-15 y CU-20; AC-02, AC-07, AC-08, AC-09, AC-10 y AC-16; ADR-0003,
ADR-0004, ADR-0007 y ADR-0012.

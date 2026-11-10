# INFO — decisiones de diseño y su justificación

Resumen de las decisiones de arquitectura de LogHunter y por qué se tomaron,
para quien evalúe o retome el proyecto sin leer los quince ADR completos.
Cada sección enlaza el ADR que registra el contexto, las alternativas
consideradas y las consecuencias aceptadas; este documento no reemplaza esos
ADR, los resume. El contrato observable completo está en
[PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md); la evidencia real de cada
decisión, en los `doc/VERIFICACION_*.md`.

## Por qué map-reduce y por qué ese alcance

Un archivo de log de GB se puede particionar por rangos de bytes sin leer su
contenido de antemano: el coordinador solo necesita el tamaño total y
aritmética entera para calcular los cortes, y cada worker alinea su propio
tramo a fin de línea. Eso separa **planificación** (barata, en el
coordinador) de **cómputo** (regex sobre millones de líneas, en los
workers), que es la base de todo lo que sigue.
→ [ADR-0001](doc/adr/0001-map-reduce-y-alcance.md).

## Dos modelos de concurrencia a propósito, no por inconsistencia

El coordinador es I/O-bound (espera sockets y resultados de Celery): un
único hilo con `asyncio` escala a muchos clientes sin el costo de un
hilo/proceso por conexión, y sin *data races* porque nunca hay dos
corrutinas ejecutando Python al mismo tiempo. Los workers son CPU-bound
(parseo con regex): el GIL impide que dos hilos calculen en paralelo dentro
de un mismo proceso, así que necesitan procesos (Celery) separados, no
hilos. Mezclar ambos en un solo modelo habría ocultado esta distinción, que
es el aporte conceptual central del proyecto frente a la cátedra.
→ [ADR-0002](doc/adr/0002-coordinador-asyncio.md),
[ADR-0003](doc/adr/0003-celery-y-redis.md).

Dentro del coordinador, el patrón "leer → `await` → escribir" sobre el
diccionario de jobs está prohibido: `asyncio` elimina las *data races* de
bytecode, pero no las carreras lógicas que cruzan un punto de cesión. Por
eso el diccionario de jobs va bajo `asyncio.Lock` sin ningún `await` dentro
de la sección crítica, y la admisión usa `asyncio.Semaphore(max_jobs)` en
vez de un contador manual.
→ [ADR-0007](doc/adr/0007-jobs-admision-y-timeout.md).

## Por qué la cola lleva coordenadas y no datos

Si cada tarea Celery llevara los bytes del tramo, procesar un log de
gigabytes significaría empujar gigabytes por Redis. En cambio se encola
`(uri, inicio, fin)`: el worker resuelve su propio acceso al archivo —
`mmap` si es `local://`, `GET Range` contra MinIO si es `s3://` — sin que el
coordinador lo intermedie. Esto es lo que permite, literalmente, que un
worker viva en una máquina sin el archivo de log: verificado con un
contenedor separado del log, real, no solo en teoría.
→ [ADR-0004](doc/adr/0004-lector-local-y-s3.md),
[doc/VERIFICACION_DISTRIBUCION.md](doc/VERIFICACION_DISTRIBUCION.md).

## Por qué cada worker alinea su propio tramo

El coordinador no lee el log: divide por aritmética. La corrección de esa
división depende de una regla simple aplicada por *cada worker* sobre su
propio tramo: si `inicio > 0`, consultar el byte previo y descartar la
primera línea si es realmente parcial; completar la última línea cuyo
inicio cae dentro de `[inicio, fin)`. Esa regla, no una coordinación entre
workers, es lo que garantiza que los registros sean disjuntos y cubran el
archivo completo — incluidos los casos límite (línea de más de 64 KiB,
línea que atraviesa varios tramos, CRLF, última línea sin salto).
→ [ADR-0005](doc/adr/0005-particionado-y-lineas.md).

## Por qué TCP con JSON por línea, y no pickle ni un framework RPC

El coordinador acepta conexiones de cualquier cliente en la red del
laboratorio; `pickle` ejecuta código arbitrario al deserializar, así que
usarlo ahí sería una puerta de ejecución remota. JSON delimitado por `\n` es
trivial de parsear de forma incremental sobre un `StreamReader` fragmentado
o con varios mensajes juntos, sin depender de un framework RPC que hubiera
sido desproporcionado para cuatro tipos de mensaje.
→ [ADR-0006](doc/adr/0006-protocolo-tcp-json.md).

## Por qué cada job tiene su propio ciclo de vida

Varios clientes piden análisis a la vez; sus parciales no pueden mezclarse.
Cada pedido recibe un `job_id` propio y su `GroupResult` de Celery; el
progreso se mide con tareas realmente completadas (`completed_count()`), no
con el tamaño del grupo, para poder fallar ante un error definitivo sin
esperar el 100% de éxitos. Un `--job-timeout` revoca solo las tareas
pendientes de ese job, sin afectar a otros clientes — verificado con
infraestructura real, no solo con dobles, en el issue #23.
→ [ADR-0007](doc/adr/0007-jobs-admision-y-timeout.md),
[doc/VERIFICACION_FALLOS.md](doc/VERIFICACION_FALLOS.md).

## Por qué un proceso indexador en vez de escribir SQLite desde el coordinador

SQLite no tolera bien escritores concurrentes, y una escritura lenta a
disco dentro del event loop del coordinador congelaría la atención de todos
los clientes (invariante 1). Delegar la escritura a un proceso hijo
dedicado, con su propia cola, es lo que permite seguir usando SQLite — sin
servidor, sin instalar nada extra — en vez de un motor cliente-servidor. El
indexador es el único escritor; el coordinador nunca toca el archivo
`.sqlite`.
→ [ADR-0008](doc/adr/0008-indexador-y-sqlite.md).

## Por qué `Value`+`Lock` para las métricas compartidas, y señales POSIX

El coordinador y el indexador son procesos distintos: comparten contadores
(jobs activos, informes emitidos/persistidos, líneas válidas) a través de
`multiprocessing.Value` con `Lock` explícito, porque `+= 1` es
*read-modify-write*, no atómico, y dos procesos incrementando a la vez
pierden actualizaciones sin el lock (verificado con incrementos concurrentes
reales en `tests/test_ipc.py`, no solo en un único proceso). `SIGUSR1` pide
un snapshot sin interrumpir el servicio; `SIGINT`/`SIGTERM` disparan un
apagado ordenado: dejar de admitir, drenar lo ya encolado, esperar al hijo
fuera del loop y recién ahí salir.
→ [ADR-0009](doc/adr/0009-ipc-y-senales.md).

## Por qué Docker Compose no reemplaza la distribución real entre hosts

Replicar el servicio `worker` en un solo host con `--scale` demuestra
capacidad de escalar el *cómputo*, pero por sí solo no prueba que
coordinador y almacenamiento no necesiten compartir máquina — para eso hace
falta un worker en otro host apuntando al mismo Redis/MinIO, documentado
explícitamente en `INSTALL.md` como un paso aparte. El indexador no es un
quinto servicio: sigue siendo el proceso hijo que el coordinador lanza
dentro de su propio contenedor, igual que en desarrollo local.
→ [ADR-0012](doc/adr/0012-despliegue-y-entorno.md).

## Por qué las pruebas de infraestructura real se saltan en vez de mockearse

Un doble de `AsyncResult` prueba la lógica de correlación de jobs, pero no
prueba que la distribución o la recuperación funcionen de verdad. Donde el
riesgo es de infraestructura (pérdida de un worker, Redis/MinIO caídos, un
worker sin el filesystem del log), las pruebas usan servicios reales y se
saltan solas con un motivo explícito si no están disponibles, en vez de
simularlos — así una corrida en verde sin Redis/MinIO nunca se confunde con
evidencia de distribución real.
→ [ADR-0013](doc/adr/0013-desarrollo-y-verificacion.md).

## Decisiones de proceso (no de arquitectura del sistema)

- **Flujo de Git por milestone**: rama de issue → rama de milestone →
  `develop` → `main`, con Conventional Commits y sin push directo a ramas de
  integración. Mantiene trazable qué issue introdujo qué cambio, exigido por
  la cátedra para visar correcciones puntuales.
  → [ADR-0014](doc/adr/0014-flujo-github-por-milestones.md).
- **Paquete en `src/` con `uv`**: evita que las pruebas encuentren el código
  fuente por accidente vía `PYTHONPATH` en vez de la instalación real, y fija
  versiones exactas con `uv.lock` para que el entorno sea reproducible.
  → [ADR-0015](doc/adr/0015-paquete-python-y-entorno.md).

## Decisiones de contenido (parser, consultas, CLI)

- **Parser y consultas combinables** (`loghunter.parser`, `loghunter.consultas`):
  cada agregador produce un `Counter` parcial por tramo; sumar esos
  parciales da el total — es la base matemática que hace que el map-reduce
  sea correcto sin un paso de coordinación extra entre workers.
  → [ADR-0011](doc/adr/0011-formato-y-consultas.md).
- **CLI con `argparse` y configuración por entorno**: flags explícitos
  priman sobre variables de entorno, que priman sobre los defaults del
  código (documentado en `PROJECT_STATEMENT.md` §12.4); evita que un
  operador combine fuentes de configuración sin saber cuál ganó.
  → [ADR-0010](doc/adr/0010-cli-y-configuracion.md).

## Un cambio de implementación no acordado como ADR: la imagen de MinIO

`minio/minio` y `quay.io/minio/minio` dejaron de descargarse sin
autenticación por un cambio de licenciamiento de MinIO entre 2024 y 2025, no
por una decisión de este proyecto. Se fijó `bitnamilegacy/minio:2025.7.23-debian-12-r5`
como alternativa verificada (misma API S3, mismo ADR-0004 vigente). Se
documentó como una corrección operativa en `INSTALL.md` y
`doc/milestone/RESUMEN_MILESTONE_3.md`, no como un ADR nuevo: no cambia
ninguna decisión de arquitectura, solo el origen de una imagen Docker.

## Qué queda fuera de estas decisiones

Las funcionalidades explícitamente excluidas del alcance (autenticación,
UI web, `tail -f`, formatos distintos de combined, HA de la infraestructura,
recuperación de jobs tras reiniciar el coordinador) no tienen ADR propio
porque no se decidieron por descarte técnico sino por alcance acordado con
la cátedra — están en [PROJECT_STATEMENT.md §3.2](doc/PROJECT_STATEMENT.md) y
se retoman como mejoras futuras en [TODO.md](TODO.md).

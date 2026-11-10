# Guía de demo (sistema completo, milestones 1 a 6)

Checklist paso a paso para mostrar en vivo lo que funciona hoy. No es
documentación de referencia (eso está en los `VERIFICACION_*.md`, con
resultados reales ya registrados); esta guía es para ejecutar en el
momento, con la aplicación cliente-servidor completa.

## Qué decir antes de arrancar

Los seis milestones están implementados y verificados con infraestructura
real: núcleo de análisis local, distribución con Celery/Redis/MinIO, el
coordinador `asyncio` + protocolo TCP/JSON + cliente CLI, persistencia en
SQLite vía un indexador hijo, señales/IPC, y despliegue con Docker Compose.
Lo que se muestra hoy **es** la aplicación real: `cliente.py` habla por TCP
con `coordinador.py`, que despacha tareas `map_chunk` a workers Celery
reales y persiste el agregado a través de `indexador.py`.

## Resumen por milestone (para explicar sin mirar código)

| Milestone | Qué agrega | Dónde está la prueba |
|---|---|---|
| 1 — base | Paquete `src/loghunter`, entorno `uv`, política de GitHub | — |
| 2 — núcleo local | `parser`, `consultas`, `generar`, `lector` (`local://`), `workers` (map-reduce en memoria, un solo proceso) | [VERIFICACION_NUCLEO.md](VERIFICACION_NUCLEO.md) |
| 3 — distribución remota | `tasks` (Celery/Redis), backend `s3://`, `analisis` (correlación y reduce) — **paralelismo real**, procesos separados por el GIL | [VERIFICACION_DISTRIBUCION.md](VERIFICACION_DISTRIBUCION.md) |
| 4 — cliente-servidor | `protocolo` (TCP/JSON por LF), `coordinador` (`asyncio`, un solo hilo) — **concurrencia sin paralelismo**, atiende N clientes | `tests/test_coordinador.py` |
| 5 — persistencia e IPC | `indexador` (proceso hijo, único escritor de SQLite), `ipc.Metricas` (`Value`+`Lock`), señales y apagado ordenado | `tests/test_indexador.py`, `tests/test_operacion.py` |
| 6 — integración y entrega | Fallos reales, Docker Compose, benchmark 1/2/4 procesos | [VERIFICACION_FALLOS.md](VERIFICACION_FALLOS.md), [VERIFICACION_INTEGRACION.md](VERIFICACION_INTEGRACION.md), [VERIFICACION_BENCHMARK.md](VERIFICACION_BENCHMARK.md) |

## Conceptos que puede preguntar el profe

**Concurrencia vs. paralelismo.** Concurrencia es *estructurar* un programa
en tareas que progresan intercaladas en el tiempo, sin que necesariamente se
ejecuten en el mismo instante (ej.: un único hilo que atiende N sockets,
turnándose entre ellos cuando cada uno espera I/O). Paralelismo es
ejecutarlas *literalmente al mismo tiempo*, lo que exige más de un núcleo o
más de una máquina. El coordinador `asyncio` es concurrente sin ser
paralelo (un solo hilo, muchas conexiones intercaladas); los workers Celery
son paralelos con la concurrencia lógica mínima (procesos independientes
que no se coordinan entre sí más que por la cola).

**Por qué LogHunter usa los dos modelos a la vez** (ADR-0002, ADR-0003):

| | Coordinador (`asyncio`) | Workers (Celery) |
|---|---|---|
| Tipo de carga | I/O-bound: espera sockets y resultados de Celery | CPU-bound: regex sobre millones de líneas |
| Modelo | Un proceso, un hilo, un *event loop* | Procesos separados (`multiprocessing`/Celery) |
| Por qué | Un hilo que espera I/O no gasta CPU; escala a muchos clientes sin el costo de un hilo/proceso por conexión | El GIL de CPython serializa el bytecode: con hilos, dos cálculos CPU-bound no corren en paralelo aunque haya núcleos libres. Con procesos, cada uno tiene su propio intérprete → paralelismo real |
| Resultado | Concurrencia sin paralelismo (necesaria) | Paralelismo real (necesario) |

**GIL (Global Interpreter Lock).** El lock interno de CPython que permite
que solo un hilo ejecute bytecode Python a la vez dentro de un mismo
proceso. No afecta I/O (se libera mientras se espera un socket o un disco),
por eso `asyncio` no lo sufre. Sí afecta cómputo puro, por eso `map_chunk`
va en procesos separados y no en hilos.

**`asyncio` en una frase.** Un único hilo con un *event loop* que mantiene
una cola de corrutinas; cuando una tarea hace `await` sobre algo que
bloquearía, cede el control y el loop atiende a otra. Nunca hay dos tareas
ejecutando Python al mismo tiempo dentro del coordinador, pero si una
corrutina hace `leer → await → escribir` sobre un diccionario compartido
(el estado de los jobs), otra puede colarse en el medio y corromperlo: por
eso esas secciones van con `asyncio.Lock`/`asyncio.Semaphore` (invariante 5,
`CLAUDE.md`).

**IPC en este proyecto.** `coordinador.py` no escribe SQLite directamente:
le delega esa escritura a `indexador.py`, un proceso hijo real lanzado con
`multiprocessing.Process`, comunicado por una `Queue`. Eso evita que una
escritura lenta a disco bloquee el *event loop* que atiende a los clientes
(IPC literal: *inter-process communication*). Los contadores compartidos
(`ipc.Metricas`) usan `Value`+`Lock` porque `+=1` no es atómico entre
procesos.

**Por qué la cola (Redis) lleva coordenadas y no datos.** Los workers
reciben `(uri, inicio, fin)`, nunca los bytes del tramo. Cada worker
resuelve su propio acceso al archivo (local o `s3://`) de forma
independiente — es lo que permite que los workers vivan en una máquina
distinta a la del log (ADR-0004), verificado real con un contenedor sin el
archivo montado.

**Idempotencia de `map_chunk`.** Celery puede reintentar una tarea si su
worker muere a mitad de camino. Para que un reintento no duplique conteos,
`map_chunk` no depende de ningún estado previo: recalcula el tramo desde
cero y devuelve siempre el mismo resultado para la misma entrada — verificado
real matando un worker a mitad de un job (`VERIFICACION_DISTRIBUCION.md`,
`VERIFICACION_FALLOS.md`).

## Plan A — sistema completo con Docker Compose (~5 minutos de setup)

Esta es la demo recomendada: muestra la aplicación cliente-servidor real,
con cómputo (workers) separado de almacenamiento (MinIO), coordinado por
una cola de tareas.

### 1. Levantar todo

```bash
docker compose build
docker compose up -d --scale worker=2
docker compose ps   # los cuatro servicios "healthy"
```

### 2. Crear el bucket (una sola vez; el operador lo crea, no el sistema)

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python -c "
import boto3
from botocore.config import Config
boto3.client('s3', endpoint_url='http://localhost:9000',
              config=Config(s3={'addressing_style': 'path'})).create_bucket(Bucket='logs-demo')
"
```

### 3. Generar el log directo en MinIO

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 S3_ENDPOINT=http://localhost:9000 \
uv run --locked python -m loghunter.generar \
    -o s3://logs-demo/access.log --size-mb 20 --ips 500 --corrupt-pct 2 --seed 7
```

### 4. Pedir el análisis con el cliente real

```bash
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  --file s3://logs-demo/access.log --query top-ips --top 10
```

Se ve el progreso en vivo (`Esperando turno...`, tramos, `Progreso: n/total`)
y el informe final: tramos, líneas válidas/inválidas, duración y el ranking.
Mensaje para el profesor: el coordinador nunca lee el archivo ni cuenta
nada — solo pidió el tamaño, calculó los cortes por aritmética y publicó
`(uri, inicio, fin)` en la cola; cada worker Celery (en su propio
contenedor, sin el log montado) resolvió su propio acceso a MinIO por rango
de bytes.

### 5. Mostrar que no depende del filesystem local

```bash
docker compose exec worker sh -c "find / -xdev -iname '*.log' 2>/dev/null"
```

Solo aparecen logs del sistema (`apt`, `dpkg`); ninguno del proyecto. Los
workers de Compose están deliberadamente aislados del log del host.

### 6. Dos clientes a la vez, sin mezclar resultados

```bash
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  --file s3://logs-demo/access.log --query status &
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  --file s3://logs-demo/access.log --query traffic &
wait
```

Cada cliente recibe su propio progreso y su propio resultado, sin mezcla
(AC-05, verificado real en `VERIFICACION_INTEGRACION.md`).

### 7. (Opcional) Señales: snapshot y cierre ordenado

```bash
docker compose kill -s SIGUSR1 coordinador   # snapshot de jobs activos en el log del contenedor
docker compose logs coordinador --tail 20
docker compose kill -s SIGTERM coordinador   # apagado ordenado: drena informes antes de salir
```

### 8. (Opcional) Recuperación tras perder un worker

`docker compose kill <servicio>` mata **todas** las réplicas de ese
servicio a la vez, no sirve para matar solo una; hay que apuntar al
contenedor concreto (`docker compose ps` para confirmar el nombre real,
normalmente `<proyecto>-worker-1`/`-2`). Con un job grande en curso
(`--size-mb 40` o más, `chunk_mb` chico para tener muchas tareas):

```bash
docker compose ps --format '{{.Name}}'   # confirmar, p. ej. final-worker-1
docker kill -s SIGKILL final-worker-1    # mata solo esa réplica
```

El job sigue con la réplica que queda y termina con el mismo agregado,
después del `visibility_timeout` configurado (default 180s; se verificó
real con un valor reducido solo para la demostración). Ver
[VERIFICACION_FALLOS.md](VERIFICACION_FALLOS.md) para el procedimiento
completo con salida real capturada (recuperación vía el coordinador real,
no solo `analisis` directo). Para que la réplica muerta vuelva:
`docker compose up -d --scale worker=2`.

### 9. Apagar al terminar

```bash
docker compose down -v
```

## Plan B — núcleo local, sin infraestructura (fallback, ~1 minuto)

Sirve si falla Docker o hay poco tiempo. No necesita Redis, MinIO ni
contenedores: es el núcleo de análisis (milestone 2) invocado directamente.

```bash
uv run --locked python -m loghunter.generar \
    -o /tmp/demo.log --size-mb 5 --ips 300 --corrupt-pct 3 --seed 42
```

```bash
uv run --locked python -c "
from pathlib import Path
from collections import Counter
from loghunter.consultas import calcular_referencia, presentar, reducir
from loghunter.workers import calcular_rangos, map_chunk

archivo = Path('/tmp/demo.log')
tamano = archivo.stat().st_size
rangos = calcular_rangos(tamano, 256 * 1024)
print(f'tamano={tamano} tareas={len(rangos)}')

for query in ('top-ips', 'status', 'urls', 'traffic'):
    parciales, validas, invalidas = [], 0, 0
    for inicio, fin in rangos:
        r = map_chunk(str(archivo), inicio, fin, query)
        parciales.append(Counter(r['contador']))
        validas += r['validas']
        invalidas += r['invalidas']
    total = reducir(parciales)
    ref, validas_ref, invalidas_ref = calcular_referencia(archivo, query)
    print(query, 'coincide=', presentar(query, total) == presentar(query, ref),
          'validas=', validas, 'invalidas=', invalidas)
"
```

Mensaje para el profesor: cada tramo se procesa de forma independiente
(`map_chunk`) y el resultado agregado coincide exacto con una referencia
secuencial que lee el archivo completo sin fragmentar — eso valida que la
alineación a fin de línea en los bordes de cada tramo no pierde ni duplica
registros (invariante 3 del diseño), sin necesitar Celery/Redis/MinIO ni
el coordinador.

## Si algo falla en el momento

- `docker compose ps` muestra un servicio sin `healthy` → `docker compose
  logs <servicio>`; revisar que los puertos 6379/8888/9000/9001 estén
  libres en el host.
- El generador o el análisis tiran `AlmacenamientoNoDisponible` → faltan
  las variables `AWS_*`/`S3_ENDPOINT` en esa terminal, o el bucket no
  existe (repetir el paso 2).
- `docker compose kill <servicio>` mata **todas** las réplicas de ese
  servicio; para matar una sola, usar `docker kill <nombre-de-contenedor>`
  con el nombre real de `docker compose ps` (ver paso 8).
- Ante cualquier falla de infraestructura, caer al Plan B: no depende de
  nada externo y ya deja ver map-reduce y la verificación contra la
  referencia secuencial.

## Referencias

PROJECT_STATEMENT completo; `doc/VERIFICACION_*.md` para la evidencia real
de cada paso que esta guía solo ejecuta en vivo.

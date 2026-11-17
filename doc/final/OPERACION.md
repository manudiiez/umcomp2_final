# Guía operativa — levantar, ejecutar y probar

Manual de la terminal para el final. Primero **cómo levantar el sistema**
según en qué estado lo encontrás, después **cada comando con sus
parámetros y lo que devuelve**, y al final una **secuencia de pruebas
para mostrarle al profesor**.

El acompañante conceptual es
[EXPLICACION_CODIGO.md](EXPLICACION_CODIGO.md) (qué hace cada archivo y
por qué). La instalación completa del entorno está en
[INSTALL.md](../../INSTALL.md); las decisiones de arquitectura y
tecnología, en [doc/adr/INDEX.md](../adr/INDEX.md).

**Todas las salidas de este documento son reales**, capturadas el
2026-11-17 contra `docker compose up -d --scale worker=2` en este
checkout. Tus números van a variar si cambiás `--size-mb`, `--seed` o la
cantidad de workers.

---

## 0. ¿En qué estado está? — arrancá por acá

| Tu situación | Andá a |
|---|---|
| Recién cloné / nunca lo levanté | **§1** completo (entorno → stack → bucket → log) |
| Ya lo levanté antes, la máquina se reinició o hice `down` | **§2** — reanudar (2 comandos) |
| Está corriendo ahora mismo y quiero usarlo | **§4** — los comandos |
| Quiero empezar de cero, borrando datos | **§3** — reset |
| No tengo Docker / falló algo y necesito un plan B | **§7** — modo local sin infraestructura |
| Quiero la secuencia para mostrarle al profe | **§8** — guía de pruebas |

Chequeo de 5 segundos de en qué estado estás:

```bash
docker compose ps
```

- Tabla **vacía** → nunca levantado o fue bajado: §1 o §2.
- Cinco filas **`healthy`** → listo para operar: §4.
- Alguna fila **sin** `healthy` → §9 (problemas comunes).

---

## 1. Levantar de cero (primera vez)

Cuatro pasos. Los dos primeros se hacen **una sola vez por máquina**; el
tercero y el cuarto, una vez por volumen de datos.

### 1.1 Entorno de Python (una vez por checkout)

```bash
cd <repo>
uv sync --locked --python 3.12
```

**Qué hace:** crea `.venv/` e instala el proyecto más sus dos
dependencias (`celery[redis]`, `boto3`) con las versiones **exactas** de
`uv.lock`.

| Parámetro | Significa |
|---|---|
| `--locked` | Usar `uv.lock` tal cual, sin re-resolver. Si el lock quedó desactualizado, **falla** en vez de cambiarte versiones por lo bajo |
| `--python 3.12` | Fija la familia del intérprete (`pyproject.toml` exige `>=3.12,<3.13`) |

**Devuelve:** una lista de paquetes instalados y
`Installed N packages in …`. Comprobación rápida:

```bash
uv run --locked python -c "import loghunter; print(loghunter.__file__)"
```

### 1.2 Levantar la infraestructura

```bash
docker compose build
docker compose up -d --scale worker=2
```

**Qué hace:** construye **una sola imagen** (`loghunter:local`) y levanta
cuatro servicios — `redis`, `minio`, `coordinador` y `worker` — con dos
réplicas de worker. El **indexador no es un servicio**: es un proceso
hijo que el coordinador lanza adentro de su propio contenedor.

| Parámetro | Significa |
|---|---|
| `-d` | *Detached*: al fondo, te devuelve la terminal |
| `--scale worker=N` | N **contenedores** worker. Cada uno corre con `-c 4` (fijado en el `command` del Compose) ⇒ **N × 4 procesos** Celery. Con `N=2` son **8** |

**Devuelve** (real):

```
 Container final-redis-1  Healthy
 Container final-minio-1  Healthy
 Container final-coordinador-1  Started
 Container final-worker-1  Started
 Container final-worker-2  Started
```

Esperá ~20 s y confirmá:

```bash
docker compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}'
```

```
NAME                  SERVICE       STATUS
final-coordinador-1   coordinador   Up 25 seconds (healthy)
final-minio-1         minio         Up 31 seconds (healthy)
final-redis-1         redis         Up 31 seconds (healthy)
final-worker-1        worker        Up 25 seconds (healthy)
final-worker-2        worker        Up 25 seconds (healthy)
```

**Los cinco tienen que decir `healthy`.** El del worker no se conforma con
que el proceso exista: hace `celery inspect ping` y exige `OK`.

Puertos que quedan publicados: **8888** coordinador, **6379** Redis,
**9000** API S3 de MinIO, **9001** consola de MinIO.

### 1.3 Exportar las credenciales en tu terminal

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_DEFAULT_REGION=us-east-1
export S3_ENDPOINT=http://localhost:9000
```

**Por qué:** `generar.py` corre **en tu host** (fuera de Compose) y
necesita hablarle a MinIO. Los contenedores ya las tienen por el
`environment:` del Compose; **tu shell no**.

> **Esto se olvida siempre.** El síntoma es
> `NoCredentialsError: Unable to locate credentials`. Y tiene que ser
> `export`: pasarlas inline a un comando anterior **no** sirve para el
> siguiente.

`minioadmin`/`minioadmin` son el default de desarrollo del Compose. No
los uses fuera del laboratorio.

### 1.4 Crear el bucket (una vez por volumen)

```bash
uv run --locked python -c "
import boto3
from botocore.config import Config
c = boto3.client('s3', endpoint_url='http://localhost:9000',
                 config=Config(s3={'addressing_style':'path'}))
c.create_bucket(Bucket='logs-demo')
print([b['Name'] for b in c.list_buckets()['Buckets']])
"
```

**Devuelve:** `['logs-demo']`

**El operador crea el bucket**, no el sistema: ni `generar.py` ni
`lector.py` lo crean. Si ya existía devuelve
`BucketAlreadyOwnedByYou` — es inofensivo, el bucket sigue ahí.

### 1.5 Generar el log de prueba

```bash
uv run --locked python -m loghunter.generar \
  -o s3://logs-demo/access.log --size-mb 20 --ips 500 --corrupt-pct 2 --seed 7
```

**Devuelve** (real, ~2.4 s):

```
Generado 20971555 bytes en s3://logs-demo/access.log (validas=190783, invalidas=3821, total=194604)
```

**Anotá esos dos números: 190783 válidas y 3821 inválidas.** Son tu
verdad de referencia: **todas** las consultas que corras sobre este
archivo tienen que devolver exactamente eso, con cualquier `--chunk-mb` y
cualquier cantidad de workers. Es la prueba de que el particionado no
pierde ni duplica.

Ya está. Andá a **§4** para operar o a **§8** para la demo.

---

## 2. Reanudar (ya lo había levantado)

Si la máquina se reinició o hiciste `docker compose down` **sin `-v`**,
los datos siguen ahí (el bucket y el SQLite viven en volúmenes
nombrados). Solo hacen falta dos cosas:

```bash
docker compose up -d --scale worker=2        # 1. levantar

export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
       AWS_DEFAULT_REGION=us-east-1 S3_ENDPOINT=http://localhost:9000   # 2. credenciales
```

**No** hace falta `build` (la imagen está), ni `uv sync` (el `.venv`
está), ni crear el bucket, ni regenerar el log.

Confirmá que el log sobrevivió:

```bash
docker compose exec minio mc alias set local http://127.0.0.1:9000 minioadmin minioadmin
docker compose exec minio mc ls local/logs-demo
```

```
[2026-11-17 15:03:58 UTC]  20MiB STANDARD access.log
```

Y que el histórico de informes sobrevivió:

```bash
docker compose exec coordinador python -c "
import sqlite3; print(sqlite3.connect('/data/informes.sqlite').execute('SELECT count(*) FROM informes').fetchone()[0])"
```

Si `build` hace falta (cambiaste código): `docker compose up -d --build
--scale worker=2`.

---

## 3. Reset desde cero

| Querés | Comando | Qué se pierde |
|---|---|---|
| Parar, conservando todo | `docker compose down` | Nada. El bucket y el SQLite quedan en los volúmenes |
| Parar y **borrar los datos** | `docker compose down -v` | El bucket de MinIO **y** el histórico de informes |
| Reconstruir la imagen (cambió el código) | `docker compose up -d --build --scale worker=2` | Nada |
| Borrar solo el histórico de informes | `docker compose exec coordinador rm /data/informes.sqlite` + reiniciar el coordinador | Los informes; el log en MinIO queda |
| Empezar **totalmente** limpio | `docker compose down -v && docker compose build --no-cache && docker compose up -d --scale worker=2` | Todo: hay que repetir §1.4 y §1.5 |

El `-v` es lo único que borra datos. Úsalo a propósito, no por costumbre:
si lo corrés, **tenés que volver a crear el bucket y regenerar el log**.

---

## 4. Los comandos, uno por uno

Cada comando trae su tabla de parámetros y, abajo, **ejemplos reales
listos para copiar**: el comando, lo que devuelve y qué mirar en la
salida. Todas las salidas de esta sección salieron de una corrida real
sobre el log de §1.5.

**Qué corre solo y qué corrés vos.** Con `docker compose up` ya están
arriba **coordinador** (§4.3) y **workers** (§4.4): no los arranques de
nuevo. Lo que ejecutás a mano desde tu terminal es siempre
**`generar`** (§4.1) y **`cliente`** (§4.2), que son CLIs de un disparo,
no servicios. Las secciones 4.3 y 4.4 están marcadas y solo hacen falta
si querés otro puerto, otros límites o sumar workers.

```
vos, a mano            ya corriendo en Compose
─────────────          ───────────────────────
generar  (§4.1)        coordinador  (§4.3)
cliente  (§4.2)        worker × 2   (§4.4)
```

Todos los ejemplos asumen §1 hecho y, para `generar`, las credenciales
exportadas (§1.3).

### 4.1 `generar` — fabricar un log de prueba

**Lo corrés vos, siempre** (también con Compose arriba): es la
herramienta que fabrica el archivo a analizar. Escribe un log *combined*
sintético por bloques —nunca junta el archivo en memoria— en disco o
directo a MinIO.

```bash
uv run --locked python -m loghunter.generar -o <destino> --size-mb <N> [opciones]
```

| Parámetro | Oblig. | Default | Qué significa |
|---|---|---|---|
| `-o`/`--out` | **Sí** | — | Destino. `ruta`, `local://ruta` o `s3://bucket/key`. **El esquema decide dónde escribe**; el bucket debe existir |
| `--size-mb` | **Sí** | — | Tamaño objetivo en MiB (`2**20`). `0` genera un archivo **vacío** (válido, no es error). Completa la última línea, así que el real queda un poco por encima |
| `--ips` | No | `5000` | Cuántas IPs distintas sortear. **Pocas IPs ⇒ `top-ips` más marcado**; útil para la demo |
| `--corrupt-pct` | No | `0` | % aproximado de líneas corruptas (IP mala, fecha mala, texto suelto). Sirve para mostrar que una línea inválida no rompe nada |
| `--seed` | No | `None` | Semilla del PRNG. **Con semilla fija, mismos argumentos ⇒ los mismos bytes exactos.** Sin ella, cada corrida difiere |

**Devuelve** una sola línea con los totales reales:

```
Generado 20971555 bytes en s3://logs-demo/access.log (validas=190783, invalidas=3821, total=194604)
```

`validas` + `invalidas` = `total` líneas. Esos números son tu **verdad
de referencia**: toda consulta sobre ese archivo tiene que devolver
exactamente eso.

Cada línea válida tiene esta forma (primera línea real de un log
generado con `--seed 1`):

```
8.199.221.156 - - [01/Jan/2026:00:00:00 -0300] "POST /login HTTP/1.1" 500 7689 "-" "loghunter-generar/1.0"
```

#### Ejemplos

**a) Un log chico en MinIO para iterar rápido.** 5 MB y solo 20 IPs, así
el `top-ips` queda bien marcado:

```bash
uv run --locked python -m loghunter.generar \
  -o s3://logs-demo/chico.log --size-mb 5 --ips 20 --corrupt-pct 5 --seed 99
```

```
Generado 5242944 bytes en s3://logs-demo/chico.log (validas=47165, invalidas=2501, total=49666)
```

Queda como un segundo objeto en el bucket, **sin tocar** `access.log`:

```bash
docker compose exec minio mc ls local/logs-demo
```

```
[2026-11-17 16:38:09 UTC]  20MiB STANDARD access.log
[2026-11-17 16:47:00 UTC] 5.0MiB STANDARD chico.log
```

Para borrarlo después: `docker compose exec minio mc rm local/logs-demo/chico.log`.

**b) Un log local, sin MinIO.** Útil para §7 (plan B) o para ver el
archivo con `head`:

```bash
uv run --locked python -m loghunter.generar -o /tmp/mini.log --size-mb 1 --ips 10 --seed 1
head -2 /tmp/mini.log
```

```
Generado 1048639 bytes en /tmp/mini.log (validas=9622, invalidas=0, total=9622)
```

`invalidas=0` porque no pasamos `--corrupt-pct`. Ojo: **este archivo no
sirve para consultar con Compose** — los contenedores no ven tu `/tmp`
(ver §6).

**c) Muchas líneas corruptas, a propósito.** Para mostrar que el parser
descarta sin caerse:

```bash
uv run --locked python -m loghunter.generar -o /tmp/sucio.log --size-mb 2 --corrupt-pct 30 --seed 3
```

```
Generado 2097184 bytes en /tmp/sucio.log (validas=16786, invalidas=7215, total=24001)
```

7215 de 24001 = **30,1 %**: el porcentaje pedido se cumple, y las 16786
válidas se procesan igual.

**d) El caso borde: archivo vacío.**

```bash
uv run --locked python -m loghunter.generar -o /tmp/vacio.log --size-mb 0
```

```
Generado 0 bytes en /tmp/vacio.log (validas=0, invalidas=0, total=0)
```

No es un error: es un archivo válido de 0 bytes. Si lo consultás, el
informe devuelve 0 tramos y resultado vacío.

**e) La prueba de `--seed`** (reproducibilidad byte a byte):

```bash
uv run --locked python -m loghunter.generar -o /tmp/a.log --size-mb 5 --seed 42
uv run --locked python -m loghunter.generar -o /tmp/b.log --size-mb 5 --seed 42
shasum -a 256 /tmp/a.log /tmp/b.log
```

```
398d8ceae2e25747f5cd0b20880374bcb2dbbab4baf24381bcb4689fdc65b252  /tmp/a.log
398d8ceae2e25747f5cd0b20880374bcb2dbbab4baf24381bcb4689fdc65b252  /tmp/b.log
```

**El mismo `sha256`, byte a byte.** Sin `--seed`, distintos. Es lo que
permite que los números de la demo sean siempre los mismos.

### 4.2 `cliente` — pedir un análisis

**Lo corrés vos, siempre.** Con Compose el coordinador ya escucha en
`127.0.0.1:8888` (puerto publicado), así que **no hace falta `-i` ni
`-p`**: los defaults apuntan ahí. Abre un socket TCP, manda **un** pedido
JSON y muestra el informe. Un pedido por ejecución.

**No necesita credenciales de MinIO.** Verificado: el cliente solo manda
la URI; quien lee el archivo son el coordinador y los workers, que
tienen sus propias credenciales por el `environment:` del Compose.

```bash
uv run --locked python -m loghunter.cliente -f <uri> -q <consulta> [opciones]
```

| Parámetro | Oblig. | Default | Qué significa |
|---|---|---|---|
| `-f`/`--file` | **Sí** | — | URI del log. `s3://bucket/key`, o una ruta local que se normaliza a `local:///abs`. **Normalizar no sube el archivo**: tiene que existir donde corren el coordinador y los workers |
| `-q`/`--query` | **Sí** | — | `top-ips` \| `status` \| `urls` \| `traffic`. Una consulta inválida muere **en el cliente** (exit 2), sin molestar al servidor |
| `-i`/`--ip` | No | `127.0.0.1` | Dirección del coordinador. Acepta IPv4 e IPv6 |
| `-p`/`--port` | No | `8888` | Puerto del coordinador |
| `--top` | No | `10` | Cuántas filas devolver. **Solo recorta `top-ips` y `urls`**; `status` y `traffic` devuelven todo siempre |
| `--chunk-mb` | No | *lo decide el coordinador* (`32`) | Sugerencia de tamaño de tramo, 1–256 MiB. **Es la perilla del paralelismo**: define cuántas tareas se generan (`ceil(tamaño/chunk)`) |
| `--raw` | No | off | stdout recibe **solo** el JSON terminal + LF; progreso y errores van a stderr. Para pipes |

**Códigos de salida:** `0` éxito · `1` error del análisis o de conexión ·
`2` argumentos inválidos.

#### Ejemplo base, línea por línea

```bash
uv run --locked python -m loghunter.cliente \
  -f s3://logs-demo/access.log -q top-ips --top 5 --chunk-mb 1
```

- `-f s3://logs-demo/access.log` → **qué archivo**, y por el esquema
  `s3://`, que los workers lo leen por `GET Range` contra MinIO.
- `-q top-ips` → **qué calcular**: ranking de IPs de origen.
- `--top 5` → me alcanzan 5 filas.
- `--chunk-mb 1` → cortá el archivo en tramos de 1 MiB ⇒ 21 tareas en
  paralelo.

**Devuelve** (real):

```
Pedido admitido.
Tramos a procesar: 21 (chunk_mb=1)
Progreso: 21/21

Archivo: s3://logs-demo/access.log (20971555 bytes)
Consulta: top-ips  top=5  chunk_mb=1
Tramos: 21  workers observados: 8
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 1.099 s

100.233.156.167  437
25.205.202.229   437
32.232.221.81    437
9.113.76.75      434
90.215.63.144    431
```

Qué mirar, en orden:

| Línea | Qué te dice |
|---|---|
| `Pedido admitido.` | El coordinador tenía cupo libre (`--max-jobs`) y arrancó ya |
| `Tramos a procesar: 21` | `ceil(20971555 / 1 MiB)` = 21 tareas publicadas en Redis |
| `Progreso: 21/21` | Avanza en vivo con `completed_count()` del `GroupResult` |
| **`workers observados: 8`** | **Cuántos procesos distintos participaron de verdad** (hostnames/PIDs únicos): 2 contenedores × `-c 4`. Es *la* evidencia de distribución |
| `Líneas válidas: 190783  inválidas: 3821` | Coincide **exacto** con lo que informó `generar` |
| `Duración: 1.099 s` | Desde que el job tomó cupo hasta terminar el reduce |
| Las filas | IP y cantidad, ordenadas por cantidad ↓ y, a igual cantidad, por clave ↑ |

#### Ejemplos

**a) Las cuatro consultas, mismo archivo.** Un ejemplo de cada una con
su salida real está en **§5**; la forma es siempre la misma, cambiando
`-q`:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q status
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q urls --top 6
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q traffic --chunk-mb 4
```

**b) `--chunk-mb`: la perilla del paralelismo.** El mismo pedido, dos
veces, cambiando **solo** ese flag:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q status                # default 32
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q status --chunk-mb 1
```

| | Tramos | workers observados | Duración | Resultado |
|---|---|---|---|---|
| `--chunk-mb 32` (default) | **1** | **1** | **3.066 s** | `200 162362` / `404 18895` / `500 9526` |
| `--chunk-mb 1` | **21** | **8** | **1.117 s** | idéntico |

**Esta es la demostración más fuerte que tenés**: 20 MB no alcanzan para
llenar un tramo de 32 MiB, así que se genera **una sola tarea** y un solo
proceso hace todo. Bajando a 1 MiB entran 8 procesos y tarda **2,7× menos**
— con el **mismo resultado exacto**. Prueba paralelismo real y
corrección del particionado en un solo golpe.

**La cuenta exacta de tramos** es `ceil(tamaño / chunk_mb)`, y tiene una
trampa que conviene saber antes de que la pregunten: el archivo **no
mide 20 MiB justos**, mide 20971555 bytes = 20 MiB **+ 35 bytes**,
porque `generar` completa la última línea en vez de cortarla. El techo
de la división agrega entonces un tramo final diminuto:

| `--chunk-mb` | Cuenta | Tramos | Comentario |
|---|---|---|---|
| `1` | `ceil(20971555 / 1048576)` | **21** | 20 tramos llenos + 1 de 35 bytes |
| `4` | `ceil(20971555 / 4194304)` | **6** | 5 llenos + 1 de 35 bytes |
| `5` | `ceil(20971555 / 5242880)` | **5** | 4 llenos + 1 de 35 bytes |
| `8` | `ceil(20971555 / 8388608)` | **3** | 2 llenos + 1 parcial de ~4 MiB |
| `32` | `ceil(20971555 / 33554432)` | **1** | el archivo entero en un tramo |

O sea: con `--chunk-mb 5` sobre este archivo salen **5 tramos, no 4**
—verificado real, `Tramos: 5  workers observados: 5`—. Ese último tramo
de 35 bytes no es un bug: contiene el final de la última línea, y el
worker que lo recibe lo procesa o lo descarta según la regla de
alineación (§3 de EXPLICACION_CODIGO.md). **Lo que importa es que el
total de líneas no cambia nunca**: 190783 válidas con cualquier chunk.

**c) Pedir otro archivo del bucket** (el de 5 MB del §4.1):

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/chico.log -q top-ips --top 3 --chunk-mb 1
```

```
Archivo: s3://logs-demo/chico.log (5242944 bytes)
Consulta: top-ips  top=3  chunk_mb=1
Tramos: 6  workers observados: 6
Líneas válidas: 47165  inválidas: 2501  total: 49666
Duración: 0.560 s

100.108.77.119  2431
2.217.104.93    2424
34.110.82.192   2416
```

Los totales coinciden con los que imprimió `generar` para `chico.log`, y
con 20 IPs las cuentas por IP son mucho más altas.

**d) `--raw` para scripts.** stdout queda con solo el JSON:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q status --raw 2>/dev/null \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['chunks'], d['workers'], d['duracion'])"
```

```
21 8 1.065029001008952
```

El JSON terminal trae: `type`, `version`, `job_id`, `file`,
`tamano_bytes`, `query`, `top`, `chunk_mb`, `chunks`, `workers`,
`lineas`, `lineas_totales`, `invalidas`, `duracion`, `resultados` (lista
de `{clave, valor}`) y `persistencia` (`"encolada"`).

**e) Los errores, con su código de salida.** Los cuatro son reales y
cada uno prueba algo distinto:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/nada.log -q status
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log.gz -q status
uv run --locked python -m loghunter.cliente -p 9999 -f s3://logs-demo/access.log -q status
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q inventada
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q status --chunk-mb 999
```

| Comando | Devuelve | Exit | Qué prueba |
|---|---|---|---|
| objeto inexistente | `Error [FILE_NOT_FOUND]: No existe el objeto S3: s3://logs-demo/nada.log` | `1` | El coordinador valida **antes** de encolar nada |
| `.gz` | `Error [UNSUPPORTED_URI]: Los logs comprimidos (.gz) no están soportados` | `1` | Exclusión declarada del alcance, con error acotado |
| puerto cerrado | `No se pudo conectar a 127.0.0.1:9999: [Errno 61] Connection refused` | `1` | Falla de red manejada, sin traceback |
| consulta inválida | `argument -q/--query: invalid choice: 'inventada' (choose from 'top-ips', 'status', 'urls', 'traffic')` | `2` | Muere **en el cliente**: el servidor ni se enteró |
| `--chunk-mb 999` | `argument --chunk-mb: --chunk-mb debe estar entre 1 y 256 MiB` | `2` | Validación de rango local |

Ninguno deja el servidor en mal estado: después de los cinco, el
coordinador sigue atendiendo normalmente.

### 4.3 `coordinador` — el servidor *(con Compose ya está corriendo)*

**No lo arranques si usás Compose.** Esta sección es para el caso
contrario: correr un **segundo** coordinador a mano, en otro puerto, con
otros límites — que es justo lo que sirve para demostrar la cola de
admisión.

```bash
uv run --locked python -m loghunter.coordinador -i 0.0.0.0 -p 8888 \
  --chunk-mb 32 --max-jobs 3 --job-timeout 300 --db informes.sqlite
```

| Parámetro | Default | Qué significa |
|---|---|---|
| `-i`/`--ip` | `0.0.0.0` | Interfaz de escucha. `0.0.0.0` = todas |
| `-p`/`--port` | `8888` | Puerto TCP |
| `--chunk-mb` | `32` | Tamaño de tramo por default, si el cliente no manda el suyo |
| `--max-jobs` | `3` | **Análisis simultáneos** (el `Semaphore`). Conexiones acepta todas; el 4.º pedido recibe `queued` y espera |
| `--job-timeout` | `300` | Segundos **desde que toma cupo** (no desde que conecta) hasta terminar el reduce. Al vencer: revoca **sus** tareas y responde `JOB_TIMEOUT` |
| `--broker` | `CELERY_BROKER_URL` | URL de Redis |
| `--s3-endpoint` | `S3_ENDPOINT` | Endpoint de MinIO |
| `--db` | `informes.sqlite` | SQLite del indexador |

#### Ejemplo: un coordinador propio que demuestra la cola

Dos tareas en una: ver el arranque en vivo y mostrar `--max-jobs` en
acción. Usa el **Redis y el MinIO del Compose** (por eso las variables),
pero su **propio puerto y su propio SQLite**, así no interfiere con el
coordinador del Compose.

```bash
export CELERY_BROKER_URL=redis://localhost:6379/0
uv run --locked python -m loghunter.coordinador -p 8889 --max-jobs 1 --chunk-mb 4 --db /tmp/manual.sqlite
```

**Devuelve** al arrancar (real):

```
INFO __main__: Coordinador escuchando en ('0.0.0.0', 8889)
INFO __main__: Indexador iniciado: pid=48369 db=/tmp/manual.sqlite
```

Esas dos líneas confirman que el socket está arriba **y** que el proceso
hijo indexador arrancó. Dejalo corriendo y, en **otra terminal**, mandá
dos clientes a la vez contra el `8889`:

```bash
uv run --locked python -m loghunter.cliente -p 8889 -f s3://logs-demo/access.log -q status --chunk-mb 1 &
uv run --locked python -m loghunter.cliente -p 8889 -f s3://logs-demo/access.log -q top-ips --top 3 --chunk-mb 1 &
wait
```

Uno entra y **el otro espera su turno** (real):

```
# cliente que llegó segundo
Esperando turno: el coordinador está al tope de análisis simultáneos...
Tramos a procesar: 21 (chunk_mb=1)
...
Duración: 1.117 s
```

```
# cliente que llegó primero
Pedido admitido.
...
Duración: 1.255 s
```

**Qué decir:** con `--max-jobs 1` el segundo pedido recibe `queued` y
espera, **pero su conexión fue aceptada igual** — el event loop no se
bloqueó. Los dos terminan bien y con los mismos totales. Con el default
`--max-jobs 3` los dos habrían arrancado juntos.

Al cerrarlo, los informes quedaron en su propio SQLite:

```bash
uv run --locked python -c "
import sqlite3; c=sqlite3.connect('/tmp/manual.sqlite')
for f in c.execute('SELECT substr(job_id,1,8), query, chunks, workers, lineas, round(duracion,2) FROM informes'): print(f)"
```

```
('39dcffcf', 'top-ips', 21, 8, 190783, 1.25)
('21735eb1', 'status', 21, 8, 190783, 1.12)
('b60035c2', 'traffic', 21, 8, 190783, 1.09)
```

### 4.4 `celery worker` — los procesos que calculan *(con Compose ya corren)*

**Con Compose ya hay 2 contenedores × 4 procesos = 8.** Esta sección es
para sumar uno a mano y ver cómo cambia el conteo.

```bash
uv run --locked celery -A loghunter.tasks worker --loglevel=INFO -c 4
```

| Parámetro | Qué significa |
|---|---|
| `-A loghunter.tasks` | Dónde está la app Celery |
| `-c N` | **Procesos del pool `prefork`** = paralelismo real dentro de este worker |
| `--loglevel=INFO` | Necesario para ver la línea `map_chunk … rango=[i,f)` por tarea |
| `-n nombre@%h` | Nombre del nodo. Hace falta si corrés varios en el mismo host |

**Los dos ejes del paralelismo, que no hay que confundir:**

| | Qué replica | Cómo se cambia |
|---|---|---|
| `--scale worker=N` | **Contenedores** (procesos Celery independientes, cada uno con su pool) | `docker compose up -d --scale worker=N` |
| `-c N` | **Procesos dentro de** cada contenedor | `command:` del servicio `worker` |

`--scale worker=2` + `-c 4` = **8 procesos**. Confirmado real:
`workers observados: 8`.

#### Ejemplo: sumar un worker en tu host y verlo aparecer

Antes de empezar, una consulta da `workers observados: 8`. Levantá un
worker más **en tu máquina**, apuntando al mismo Redis y al mismo MinIO
del Compose:

```bash
export CELERY_BROKER_URL=redis://localhost:6379/0
uv run --locked celery -A loghunter.tasks worker --loglevel=INFO -c 2 -n local@%h
```

```
- ** ---------- .> transport:   redis://:**@localhost:6379/0
- *** --- * --- .> concurrency: 2 (prefork)
[INFO/MainProcess] local@MacBook-Pro-de-Manuel.local ready.
```

Ahora, en otra terminal, la **misma** consulta de antes:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q top-ips --top 3 --chunk-mb 1
```

```
Tramos: 21  workers observados: 10
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 1.100 s
```

**8 → 10.** Los 2 procesos nuevos se sumaron al reparto sin reiniciar ni
reconfigurar nada: ni el coordinador ni el cliente saben cuántos workers
hay. En el log del worker local se ve qué tramo le tocó:

```
[SpawnPoolWorker-1] map_chunk job_id=61cdcd85… uri=s3://logs-demo/access.log rango=[19922944,20971520) query=top-ips
```

Ese worker **no tiene el archivo**: lo leyó por `GET Range` desde MinIO,
pidiendo solo su tramo. Cerralo con `Ctrl-C` cuando termines.

Ninguno de los dos ejes simula varias **máquinas**: para eso hace falta un
worker en otro host apuntando al mismo Redis/MinIO (ver
[INSTALL.md](../../INSTALL.md), "Worker externo en otro host").

### 4.5 Inspección — dónde ver cada cosa

Todo lo de acá es de solo lectura: podés correrlo en cualquier momento,
incluso con jobs en vuelo.

**Informes persistidos en SQLite** (los escribió el indexador, no el
coordinador):

```bash
docker compose exec coordinador python -c "
import sqlite3
conn = sqlite3.connect('/data/informes.sqlite')
for f in conn.execute('SELECT substr(job_id,1,8), query, chunks, workers, lineas, invalidas, round(duracion,2) FROM informes ORDER BY id DESC LIMIT 5'):
    print(f)
print('informes:', conn.execute('SELECT count(*) FROM informes').fetchone()[0])
"
```

```
('61cdcd85', 'top-ips', 21, 10, 190783, 3821, 1.1)
('f180a33b', 'top-ips', 6, 6, 47165, 2501, 0.56)
('9c4e652f', 'traffic', 6, 5, 190783, 3821, 1.08)
('c281ae6c', 'traffic', 6, 6, 190783, 3821, 1.07)
('b6cf6f2c', 'urls', 1, 1, 190783, 3821, 2.53)
informes: 28
```

Se lee como el historial de la demo: el `top-ips` con 10 workers es el
del §4.4, el de 47165 líneas es el de `chico.log`, y el `urls` con
`chunks=1, workers=1` es el que corrió con el chunk default. **Cada fila
es un job distinto, con su `job_id` propio** — nunca se mezclan.

**Objetos en MinIO** (la consola web del `:9001` **no responde** en la
imagen `bitnamilegacy` que usa el Compose — verificado; usá `mc`):

```bash
docker compose exec minio mc alias set local http://127.0.0.1:9000 minioadmin minioadmin
docker compose exec minio mc ls local/logs-demo
```

```
[2026-11-17 16:38:09 UTC]  20MiB STANDARD access.log
[2026-11-17 16:47:00 UTC] 5.0MiB STANDARD chico.log
```

**Logs:**

```bash
docker compose logs coordinador --tail 20      # admisión de jobs, indexador, snapshots
docker logs final-worker-1 2>&1 | grep map_chunk | tail -3    # qué tramo tocó a qué proceso
```

```
[ForkPoolWorker-3] map_chunk job_id=69642239… task_id=7c5ec445… rango=[13631488,14680064) query=traffic
[ForkPoolWorker-1] map_chunk job_id=69642239… rango=[14680064,15728640) query=traffic
[ForkPoolWorker-2] map_chunk job_id=69642239… rango=[16777216,17825792) query=traffic
```

Fijate: los rangos están **exactamente a 1 MiB** (1048576 bytes) uno de
otro, y los de un mismo proceso **no son contiguos** (`14680064` y
después `16777216`) — los del medio se los llevaron otros procesos. Eso
es el reparto ocurriendo.

### 4.6 Señales

```bash
docker compose kill -s SIGUSR1 coordinador   # snapshot de estado
docker compose kill -s SIGTERM coordinador   # apagado ordenado
```

(Si corrés el coordinador a mano, es `kill -USR1 <pid>` y `kill -TERM <pid>`.)

#### Ejemplo: snapshot con un job en vuelo

Lanzá una consulta y, menos de un segundo después, pedile el snapshot:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q traffic --chunk-mb 1 &
sleep 0.6
docker compose kill -s SIGUSR1 coordinador
wait
docker compose logs coordinador --tail 5
```

**Devuelve** (real):

```
snapshot (SIGUSR1): 1 job(s) activo(s)
  job_id=b60035c2e86042cbb7f441fc206137f1 estado=running progreso=0/21 transcurrido=0.5s
snapshot (SIGUSR1): contadores={'jobs_activos': 1, 'informes_emitidos': 2, 'informes_persistidos': 2, 'lineas_validas': 381566}
```

**Qué decir:** `SIGUSR1` **no interrumpe nada** — loguea y sigue; el
cliente terminó su consulta normalmente. Y
`informes_emitidos == informes_persistidos` es la señal de que el
indexador no quedó atrasado: nada sin guardar.

#### Ejemplo: apagado ordenado

```bash
docker compose kill -s SIGTERM coordinador
docker compose logs coordinador --tail 2
```

```
INFO __main__: Apagado solicitado: dejo de admitir conexiones nuevas
```

Deja de admitir, avisa `SERVER_SHUTDOWN` a los clientes activos, encola
el centinela para el indexador y lo espera hasta 30 s. Los informes ya
emitidos quedan en SQLite. Para volver a levantarlo:
`docker compose up -d coordinador`.

### 4.7 Pruebas

```bash
uv run --locked python -m pytest -q
```

**Importante: bajá Compose antes de correr la suite.** Los tests de
integración usan contenedores con nombres fijos (`loghunter-redis`,
`loghunter-minio`) y los paran a propósito para probar fallos; si Compose
está arriba, sus workers compiten por la misma cola. Verificado real con
Compose arriba:

```
12 failed, 205 passed in 49.63s
```

No es una regresión: son los tests de fallos que no encuentran los
contenedores que esperan, más los `local://` que agarró un worker del
Compose. Los tres escenarios válidos están en §6:

| Escenario | Resultado esperado |
|---|---|
| Sin Redis ni MinIO | `201 passed, 16 skipped` |
| `loghunter-redis`/`-minio` manuales, sin worker | `205 passed, 12 skipped` |
| Igual + un worker standalone | `217 passed, 0 skipped` |

Los `skipped` **no se simulan**: se saltan solos cuando no hay
infraestructura real.

### 4.8 Benchmark

```bash
uv run --locked python scripts/benchmark_procesos.py
```

Necesita Redis real y **que no haya otros workers compitiendo** (bajá los
del Compose). Genera 48 MB con semilla fija, corre `-c 1`, `-c 2` y
`-c 4` (3 repeticiones cada uno) y verifica con un `assert` que **el
agregado es idéntico** en las nueve corridas. Resultado registrado:

| Procesos | Mediana | Speedup |
|---|---|---|
| 1 | 8.771 s | 1.00 |
| 2 | 5.186 s | 1.69 |
| 4 | 2.635 s | **3.33** |

**Qué decir:** 3,33× con 4 procesos, y el resultado no cambió nunca. Si
el trabajo fuera I/O-bound o estuviera serializado, el speedup sería
~1×; si el particionado estuviera mal, el agregado cambiaría entre
corridas.

---

## 5. Las cuatro consultas

Acá va **un ejemplo ejecutable de cada una** con su salida real sobre el
log de 20 MB de §1.5. Todas se piden igual, cambiando solo `-q`; los
agregadores están en EXPLICACION_CODIGO.md, archivo 8 (`consultas.py`).

| Consulta | Qué mira | Acumula | `--top` | Orden |
|---|---|---|---|---|
| `top-ips` | IP de origen (normalizada con `ipaddress`) | `+1` | **Sí** | Cantidad ↓, clave ↑ |
| `status` | Código HTTP | `+1` | No | Numérico ↑ |
| `urls` | Destino **con** query string (`/a?x=1` ≠ `/a`) | `+1` | **Sí** | Cantidad ↓, clave ↑ |
| `traffic` | Hora UTC truncada | **`+bytes`** | No | Cronológico ↑ |

### `top-ips` — quién pega más

```bash
uv run --locked python -m loghunter.cliente \
  -f s3://logs-demo/access.log -q top-ips --top 5 --chunk-mb 1
```

```
Tramos: 21  workers observados: 8
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 1.099 s

100.233.156.167  437
25.205.202.229   437
32.232.221.81    437
9.113.76.75      434
90.215.63.144    431
```

**Cómo leerlo:** 437 peticiones de esa IP. Con `--ips 500` sobre 190 783
líneas, el promedio por IP es ~382, así que el top está apenas por
encima — es un sorteo uniforme, no un atacante. **Las tres primeras
empatan en 437 y se ordenan por IP ascendente**: el desempate es
determinista, no por orden de llegada de los parciales. Si querés un top
más marcado, generá con `--ips 20` (§4.1 ejemplo a).

### `status` — salud del servicio

```bash
uv run --locked python -m loghunter.cliente \
  -f s3://logs-demo/access.log -q status --chunk-mb 1
```

```
Tramos: 21  workers observados: 8
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 1.117 s

200  162362
404  18895
500  9526
```

**Cómo leerlo:** 162362 + 18895 + 9526 = **190783**, exactamente las
válidas. Las proporciones (85 % / 10 % / 5 %) son las que `generar`
sortea a propósito. `--top` **no lo recorta**: si pedís `--top 3`
devuelve 3 filas porque solo hay 3 códigos, no porque los haya cortado.

### `urls` — qué rutas se piden

```bash
uv run --locked python -m loghunter.cliente \
  -f s3://logs-demo/access.log -q urls --top 6
```

```
Tramos: 1  workers observados: 1
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 2.528 s

/               32007
/static/app.js  31987
/api/users      31909
/login          31831
/api/orders     31576
/health         31473
```

**Cómo leerlo:** son las 6 rutas que `generar` sortea, repartidas casi en
partes iguales (~31,9 k cada una, suma 190783). Fijate que acá **no pasé
`--chunk-mb`**: tomó el default de 32 MiB, salió **1 tramo y 1 worker**, y
tardó 2,5 s en vez de 1,1 s. Mismo resultado, sin paralelismo.

### `traffic` — bytes por hora

```bash
uv run --locked python -m loghunter.cliente \
  -f s3://logs-demo/access.log -q traffic --chunk-mb 4
```

```
Tramos: 6  workers observados: 5
Líneas válidas: 190783  inválidas: 3821  total: 194604
Duración: 1.077 s

2026-01-01T03:00:00Z  4755503
2026-01-01T04:00:00Z  4837149
2026-01-01T05:00:00Z  4944793
2026-01-01T06:00:00Z  4797575
...
```

**Cómo leerlo:** es la única consulta que **suma bytes** en vez de
contar. Devuelve **123 filas** (todas las horas del log) incluso con
`--top 10`: recortar una serie temporal no tendría sentido. El pico real
es `2026-01-03T13:00:00Z → 14730119` contra ~4,8 M de una hora normal:
son las horas pico (9/10/20/21 local) que `generar` carga a propósito.

Para verlo completo sin que el terminal te tape las filas:

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q traffic --chunk-mb 4 --raw 2>/dev/null \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('filas:', len(d['resultados']))
mx=max(d['resultados'], key=lambda r: r['valor'])
print('pico:', mx['clave'], mx['valor'])
"
```

```
filas: 123
pico: 2026-01-03T13:00:00Z 14730119
```

### Lo que conviene saber explicar

- **`--top` solo recorta `top-ips` y `urls`.** Verificado con `--top 3`:
  `top-ips` → 3 filas, `urls` → 3 filas, `traffic` → **123 filas**.
- **El top se calcula después de reducir, nunca dentro de un worker.** Un
  worker no puede saber si su máximo local es global. Por eso el
  desempate es por clave ascendente y es reproducible.
- **Las 3821 inválidas aparecen en las cuatro consultas, siempre igual.**
  Se cuentan pero no se agregan: una línea corrupta no rompe el job ni
  desplaza un registro válido.

---

## 6. Modos de ejecución

Dos ejes que conviene no mezclar: **qué tan lejos puede estar el
archivo** y **quién levanta la infraestructura**.

### Backend del archivo: `local://` vs `s3://`

| | `local://` | `s3://` |
|---|---|---|
| Lectura | `mmap` del archivo | `GET Range` HTTP contra MinIO |
| Dónde debe existir | En el filesystem del **worker** que procese el tramo (y del coordinador, que pide el tamaño) | En un bucket alcanzable por red desde el worker |
| Workers en otra máquina | Solo con filesystem compartido (NFS, bind mount) | **Sí** — es el caso pensado para eso (ADR-0004) |
| Requiere MinIO | No | Sí |

**El esquema de la URI decide el backend**, no un flag: `lector.abrir()`
mira el prefijo.

**Con Compose, `local://` falla** — y es didáctico. Verificado real:

```
Error [FILE_NOT_FOUND]: No existe el archivo local: /tmp/.../demo.log
```

El coordinador corre en un contenedor que no tiene ese path. Y los
workers tampoco:

```bash
docker compose exec worker sh -c "find / -xdev -iname '*.log' 2>/dev/null"
# solo /var/log/apt/*.log, /var/log/dpkg.log — ninguno del proyecto
```

**Ese error es la demostración de que la distribución es real**: los
workers están deliberadamente aislados del filesystem del log, y por eso
`s3://` funciona y `local://` no.

### Infraestructura: Compose vs contenedores manuales

| | Docker Compose | `docker run --name loghunter-redis/-minio` |
|---|---|---|
| Para qué | Operar la app completa | Correr los **tests de integración real** |
| Nombres | `final-redis-1`, `final-minio-1`, … (los decide Compose por el nombre de carpeta) | `loghunter-redis`, `loghunter-minio` — fijos, los tests los dan por hecho |
| Workers | En contenedores, siempre arriba | Los lanza cada test, o uno standalone a mano |

**No conviven en el mismo Redis.** Si Compose está arriba y corrés
`pytest`, los workers de Compose compiten por la misma cola que los de
los tests y pueden agarrar una tarea con un `local://` que solo existe
en tu host. Bajá uno antes de levantar el otro. El síntoma exacto está
en §9.

### Modos de prueba (pytest)

La suite es siempre la misma; cambia cuánto corre según qué encuentre
vivo. Verificado real en los tres escenarios:

| Escenario | Resultado |
|---|---|
| Sin Redis ni MinIO | `201 passed, 16 skipped` — los 16 se saltan solos |
| `loghunter-redis`/`-minio` manuales + `AWS_*`/`CELERY_*`/`S3_ENDPOINT` exportadas, sin worker | `205 passed, 12 skipped` |
| Igual + un worker standalone | `217 passed, 0 skipped` |

Los 12 que faltan en el escenario intermedio (en
`test_integracion_distribucion.py` y
`test_integracion_cliente_servidor.py`) **esperan un worker ya
consumiendo la cola**, a diferencia de los de `test_fallos.py` que lanzan
el suyo. **Las variables tienen que estar `export`adas en la shell que
invoca `pytest`.**

```bash
export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
       AWS_DEFAULT_REGION=us-east-1 S3_ENDPOINT=http://localhost:9000 \
       CELERY_BROKER_URL=redis://localhost:6379/0 \
       CELERY_RESULT_BACKEND=redis://localhost:6379/1
uv run --locked python -m pytest -q
```

---

## 7. Plan B — sin Docker ni infraestructura

Si Docker falla o hay poco tiempo. Es el núcleo de análisis invocado
directo: **no** necesita Redis, MinIO ni el coordinador. Muestra
map-reduce y la verificación contra la referencia secuencial, que es el
argumento de corrección del particionado.

```bash
uv run --locked python -m loghunter.generar -o /tmp/demo.log \
  --size-mb 5 --ips 300 --corrupt-pct 3 --seed 42
```

```
Generado 5242958 bytes en /tmp/demo.log (validas=47482, invalidas=1454, total=48936)
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
        validas += r['validas']; invalidas += r['invalidas']
    ref, _, _ = calcular_referencia(archivo, query)
    print(query, 'coincide=', presentar(query, reducir(parciales)) == presentar(query, ref),
          'validas=', validas, 'invalidas=', invalidas)
"
```

**Devuelve** (real):

```
tamano=5242958 tareas=21
top-ips coincide= True validas= 47482 invalidas= 1454
status coincide= True validas= 47482 invalidas= 1454
urls coincide= True validas= 47482 invalidas= 1454
traffic coincide= True validas= 47482 invalidas= 1454
```

**Qué decir:** el archivo se partió en 21 tramos procesados de forma
independiente, y el agregado coincide **exacto** con una referencia que
lee el archivo completo sin fragmentar. Eso prueba que la alineación a
fin de línea en los bordes no pierde ni duplica registros — sin
necesitar Celery, Redis, MinIO ni el coordinador.

---

## 8. Guía de pruebas para la demo

Nueve pruebas en orden creciente. Cada una: el comando, lo que devuelve y
**la frase** que la justifica. Asumen §1 hecho y las credenciales
exportadas.

### Prueba 1 — Todo arriba

```bash
docker compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}'
```

→ Cinco filas `healthy`. **"Redis, MinIO, el coordinador y dos réplicas
de worker. El indexador no está en la lista porque es un proceso hijo
del coordinador, no un servicio."**

### Prueba 2 — Una consulta de punta a punta

```bash
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  -f s3://logs-demo/access.log -q top-ips --top 10
```

→ `Tramos: 1 · workers observados: 1 · 190783 válidas · 4.032 s`

**"20 MB con tramos de 32 MB por default da un solo tramo: un worker.
Sirve como línea de base."**

### Prueba 3 — La perilla del paralelismo *(la más importante)*

```bash
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  -f s3://logs-demo/access.log -q top-ips --top 5 --chunk-mb 1
```

→ `Tramos: 21 · workers observados: 8 · 190783 válidas · 1.108 s`

**"Mismo archivo, mismo resultado: 190783 válidas y 3821 inválidas
idénticas. Pero 21 tramos repartidos entre 8 procesos, y 4.03 s → 1.11 s.
`--chunk-mb` no cambia el resultado, cambia cuántas tareas se generan."**
Es el punto fuerte de la demo: **el agregado es invariante al
particionado**.

### Prueba 4 — Las cuatro consultas

Primero el contenido de cada una:

```bash
for q in top-ips status urls traffic; do
  echo "--- $q ---"
  uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
    -f s3://logs-demo/access.log -q $q --top 3 --chunk-mb 1 2>&1 | tail -4
done
```

```
--- top-ips ---      --- status ---     --- urls ---              --- traffic ---
100.233.156.167 437  200  162362        /               32007     …T03:00:00Z 4806193
25.205.202.229  437  404   18895        /static/app.js  31987     …T04:00:00Z 4794104
32.232.221.81   437  500    9526        /api/users      31909     …T05:00:00Z  780609
```

Y ahora la prueba de que `--top` no las recorta todas — contando filas:

```bash
for q in top-ips status urls traffic; do
  n=$(uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
        -f s3://logs-demo/access.log -q $q --top 3 --chunk-mb 1 --raw 2>/dev/null \
      | python3 -c "import json,sys; print(len(json.load(sys.stdin)['resultados']))")
  printf "  %-8s --top 3 -> %s filas\n" "$q" "$n"
done
```

```
  top-ips  --top 3 -> 3 filas
  status   --top 3 -> 3 filas
  urls     --top 3 -> 3 filas
  traffic  --top 3 -> 123 filas
```

**"`--top` solo recorta `top-ips` y `urls`. `traffic` devuelve sus 123
horas completas aunque pedí 3, porque recortar una serie temporal no
tiene sentido. Y `status` da 3 filas no porque lo haya recortado, sino
porque el log solo tiene esos 3 códigos."**

Y en `traffic` se ven los picos que `generar` mete a propósito: las
horas de carga alta dan ~14,4 M de bytes contra ~4,8 M del resto.

### Prueba 5 — El reparto, visto en los logs

```bash
docker logs final-worker-1 2>&1 | grep -o "rango=\[[0-9]*,[0-9]*) query=[a-z-]*" | tail -4
```

```
rango=[13631488,14680064) query=traffic
rango=[14680064,15728640) query=traffic
rango=[16777216,17825792) query=traffic
rango=[19922944,20971520) query=traffic
```

**"Rangos de exactamente 1 MiB, y no son contiguos: los del medio se los
llevaron los otros procesos. Por la cola viajaron estas coordenadas, no
los bytes del archivo."**

### Prueba 6 — Los workers no tienen el archivo

```bash
docker compose exec worker sh -c "find / -xdev -iname '*.log' 2>/dev/null"
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 -f /tmp/demo.log -q status
```

→ Solo logs de `apt`/`dpkg`; y el segundo comando da
`Error [FILE_NOT_FOUND]`.

**"Los workers no comparten filesystem con el log: leen por `GET Range`
contra MinIO. Un `local://` falla justamente porque la distribución es
real, no simulada."**

### Prueba 7 — Dos clientes concurrentes

```bash
( uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
    -f s3://logs-demo/access.log -q status --chunk-mb 1 --raw 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('A',d['query'],d['job_id'][:8],d['lineas'])" ) &
( uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
    -f s3://logs-demo/access.log -q traffic --chunk-mb 1 --raw 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('B',d['query'],d['job_id'][:8],d['lineas'])" ) &
wait
```

```
A status  e14040bc 190783
B traffic 69642239 190783
```

**"Un solo hilo en el coordinador atendiendo dos clientes a la vez, cada
uno con su `job_id` y su resultado. Los parciales no se mezclan: eso es
concurrencia sin paralelismo."**

Y si querés mostrar el **control de admisión**, levantá un coordinador
con cupo 1:

```bash
uv run --locked python -m loghunter.coordinador -i 127.0.0.1 -p 8889 \
  --chunk-mb 1 --max-jobs 1 --db /tmp/local.sqlite &
# ...dos clientes contra el 8889...
```

→ El segundo imprime
`Esperando turno: el coordinador está al tope de análisis simultáneos...`
**"Espera sin publicar tareas: no se ocupa Redis con trabajo que todavía
no tiene turno."** Acordate de matarlo después (`pkill -f
loghunter.coordinador`).

### Prueba 8 — Errores acotados

```bash
uv run --locked python -m loghunter.cliente -f s3://logs-demo/nada.log -q status;      echo "exit=$?"
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log.gz -q status;  echo "exit=$?"
uv run --locked python -m loghunter.cliente -p 9999 -f s3://logs-demo/access.log -q status; echo "exit=$?"
uv run --locked python -m loghunter.cliente -f s3://logs-demo/access.log -q inventada;  echo "exit=$?"
```

```
Error [FILE_NOT_FOUND]: No existe el objeto S3: s3://logs-demo/nada.log          exit=1
Error [UNSUPPORTED_URI]: Los logs comprimidos (.gz) no están soportados          exit=1
No se pudo conectar a 127.0.0.1:9999: [Errno 61] Connection refused              exit=1
error: argument -q/--query: invalid choice: 'inventada'                          exit=2
```

**"Cada fallo tiene su código del contrato y el servidor sigue
atendiendo. El `.gz` se rechaza de entrada porque no se puede leer un
rango de un comprimido sin descomprimir todo lo anterior. Y la consulta
inválida muere en el cliente, sin molestar al servidor."**

### Prueba 9 — Persistencia y señales

```bash
docker compose exec coordinador python -c "
import sqlite3
conn = sqlite3.connect('/data/informes.sqlite')
for jid, q, top in conn.execute('SELECT job_id, query, top FROM informes WHERE query=\"top-ips\" ORDER BY id DESC LIMIT 1'):
    n = conn.execute('SELECT count(*) FROM resultados WHERE informe_id=(SELECT id FROM informes WHERE job_id=?)',(jid,)).fetchone()[0]
    print(f'--top pedido={top}  filas guardadas={n}')
"
docker compose kill -s SIGUSR1 coordinador && sleep 2 && docker compose logs coordinador --tail 3
```

```
--top pedido=5  filas guardadas=500

snapshot (SIGUSR1): 1 job(s) activo(s)
  job_id=040552ad… estado=running progreso=0/21 transcurrido=1.0s
snapshot (SIGUSR1): contadores={'jobs_activos': 1, 'informes_emitidos': 9, 'informes_persistidos': 9, 'lineas_validas': 1717047}
```

**"Al cliente le fueron 5 filas, pero a SQLite se guardaron las 500: el
histórico sirve para cualquier `top` posterior sin reprocesar. Y
`SIGUSR1` da un snapshot del estado sin reiniciar nada ni interrumpir al
cliente — `emitidos == persistidos` dice que no quedó nada sin
guardar."**

### Cierre

```bash
docker compose down        # conserva el bucket y el histórico
```

---

## 9. Problemas comunes

| Síntoma | Causa | Solución |
|---|---|---|
| `NoCredentialsError: Unable to locate credentials` | Faltan las `AWS_*` **exportadas** en esta terminal | §1.3. No alcanza pasarlas inline a un comando anterior |
| `workers observados: 1` y esperabas más | `--chunk-mb` demasiado grande ⇒ **un solo tramo** | Bajá `--chunk-mb` (con 20 MB, `--chunk-mb 1` da 21 tramos) |
| `Error [FILE_NOT_FOUND]` con una ruta local | Con Compose, coordinador y workers no ven tu filesystem | Usá `s3://`, o corré todo a mano fuera de Docker |
| `docker compose up` falla por puertos | `6379`/`8888`/`9000`/`9001` ocupados por contenedores sueltos de otra sesión | `docker ps` y pará el que sobra (`docker rm -f loghunter-redis loghunter-minio`) |
| `BucketAlreadyOwnedByYou` | El bucket ya existía | Inofensivo, seguí |
| La consola de MinIO (`:9001`) no responde | La imagen `bitnamilegacy` no sirve el Object Browser en este build (verificado) | Usá `mc` (§4.5) |
| `TareaFallida: … FileNotFoundError: /private/var/.../access.log` en un test | Compose y la suite compiten por el **mismo Redis**: un worker de contenedor agarró una tarea `local://` del host | `docker compose down` antes de correr `pytest` con infraestructura real |
| Los tests de `test_fallos.py` fallan con "debía quedar inalcanzable tras `docker stop`" | Esperan los contenedores **manuales** `loghunter-redis`/`-minio`, no los de Compose | Bajá Compose y levantá los manuales de [INSTALL.md](../../INSTALL.md) |
| `docker compose down -v` borró todo | `-v` elimina los volúmenes | Repetir §1.4 y §1.5 |

---

## 10. Chuleta

```
ESTADO       docker compose ps                      → 5 filas healthy
DE CERO      uv sync --locked --python 3.12
             docker compose build && docker compose up -d --scale worker=2
             export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
                    AWS_DEFAULT_REGION=us-east-1 S3_ENDPOINT=http://localhost:9000
             crear bucket (§1.4) + generar log (§1.5)
REANUDAR     docker compose up -d --scale worker=2 + export (§2)
RESET        docker compose down        (conserva datos)
             docker compose down -v     (BORRA bucket + informes)

GENERAR      python -m loghunter.generar -o s3://logs-demo/access.log \
                    --size-mb 20 --ips 500 --corrupt-pct 2 --seed 7
CONSULTAR    python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
                    -f s3://logs-demo/access.log -q top-ips --top 10 --chunk-mb 1
             -q: top-ips | status | urls | traffic      --raw: JSON a stdout
             --chunk-mb: la perilla del paralelismo (tramos = ceil(tamaño/chunk))
             --top: SOLO recorta top-ips y urls         exit: 0 ok · 1 error · 2 args

VER          docker compose logs coordinador --tail 20
             docker logs final-worker-1 | grep map_chunk
             docker compose exec coordinador python -c "...informes.sqlite..."   (§4.5)
             docker compose exec minio mc ls local/logs-demo
SEÑALES      docker compose kill -s SIGUSR1 coordinador    (snapshot, no interrumpe)
             docker compose kill -s SIGTERM coordinador    (apagado ordenado)
TESTS        uv run --locked python -m pytest -q     → 201 passed, 16 skipped (sin infra)
PLAN B       §7 — núcleo local, sin Docker

NÚMEROS DEL LOG DE 20 MB (--seed 7): 190783 válidas · 3821 inválidas
  deben coincidir con CUALQUIER --chunk-mb y CUALQUIER cantidad de workers
  1 tramo / 1 worker = 4.03 s    ·    21 tramos / 8 workers = 1.11 s
```

---

## Referencias

[EXPLICACION_CODIGO.md](EXPLICACION_CODIGO.md) ·
[INSTALL.md](../../INSTALL.md) · [INFO.md](../../INFO.md) ·
[Índice de ADR](../adr/INDEX.md) ·
[ADR-0005](../adr/0005-particionado-y-lineas.md) ·
[README.md](../../README.md)

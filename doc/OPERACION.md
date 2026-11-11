# Referencia operativa

Respuestas a lo que suele preguntarse al ejecutar el sistema a mano, fuera
del guion de [GUIA_DEMO.md](GUIA_DEMO.md): qué calcula cada consulta, qué
flags tiene cada CLI y sus defaults, cómo escalar workers, dónde ver cada
resultado intermedio y cómo lee el archivo cada worker. No repite el
procedimiento paso a paso de la demo ni la instalación completa de
[INSTALL.md](../INSTALL.md); referencia ambos donde se superponen.

Todo lo que sigue se verificó corriendo los comandos reales contra
`docker compose up -d --scale worker=2` (2026-11-11), no solo leyendo el
código.

## Las cuatro consultas

PROJECT_STATEMENT §7.2 es el contrato normativo; esta tabla resume qué
mirar del registro y un ejemplo real:

| Consulta | Campo del registro | Usa `--top` | Orden de presentación | Ejemplo |
|---|---|---|---|---|
| `top-ips` | IP de origen, normalizada con `ipaddress` | Sí | Mayor cantidad primero | `--query top-ips --top 3` → `59.127.97.41  1038` |
| `status` | Código de estado HTTP | No (siempre todos) | Numérico ascendente | `200  40523`, `404  4937`, `500  2337` |
| `urls` | Destino solicitado tal como aparece (con query string) | Sí | Mayor cantidad primero | `--query urls --top 3` → `/login  8014` |
| `traffic` | Hora UTC truncada del timestamp (`2026-01-02T04:00:00Z`) | No (siempre todas) | Cronológico ascendente | `2026-01-02T04:00:00Z  4841592` |

Verificado real: con `--top 3`, `status` y `traffic` devolvieron igual
todos sus valores (8 horas en `traffic`, 3 códigos en `status` porque solo
hay 3), confirmando que `--top` no los recorta (PROJECT_STATEMENT §7.2).
`--top` sí recortó `top-ips` y `urls` a 3 filas.

Cada consulta corresponde a un agregador en `consultas.py` (`_clave_valor`):
`top-ips`/`status`/`urls` cuentan ocurrencias (valor `1` por registro),
`traffic` suma `bytes`. El top se calcula **después** de reducir los
parciales de todos los tramos, nunca dentro de un worker — por eso un
empate se ordena por clave ascendente, no por orden de llegada.

## Defaults de las CLI

### `cliente.py`

| Flag | Obligatorio | Default si se omite |
|---|---|---|
| `-f`/`--file` | Sí | — (error de argumentos, código de salida 2) |
| `-q`/`--query` | Sí | — (`choices` limita a las cuatro consultas) |
| `-i`/`--ip` | No | `127.0.0.1` |
| `-p`/`--port` | No | `8888` |
| `--top` | No | `10` |
| `--chunk-mb` | No | ninguno propio: si se omite, **decide el coordinador** con su propio default (`32`) |
| `--raw` | No | desactivado; con progreso/diagnósticos por stdout en vez de stderr |

Verificado real: una ruta local relativa pasada a `--file` se normaliza a
`local:///ruta/absoluta` desde el cwd del cliente (no sube el archivo);
debe existir en el host donde corre el coordinador, no en el del cliente.

### `generar.py`

| Flag | Obligatorio | Default si se omite |
|---|---|---|
| `-o`/`--out` | Sí | — |
| `--size-mb` | Sí | — (`0` genera un archivo vacío, no es error) |
| `--ips` | No | `5000` |
| `--corrupt-pct` | No | `0` (sin corrupción) |
| `--seed` | No | `None` (no reproducible entre corridas; usar un valor fijo en pruebas) |

El bucket de `-o s3://bucket/...` debe existir antes de generar — ni
`generar.py` ni `lector.py` lo crean (ver "Problemas comunes" abajo).

## Escalar el paralelismo de workers

Dos ejes independientes, verificados reales con `--scale worker=2` (cada
réplica con `-c 4` fijado en `docker-compose.yml`):

- **`docker compose up -d --scale worker=N`** replica el *contenedor*
  worker en este host: `N` procesos Celery independientes (`celery@<hash
  de contenedor>`), cada uno con su propio pool interno. Confirmado real:
  `celery -A loghunter.tasks inspect active_queues` reportó dos nodos
  distintos (`celery@def6d8ac8636`, `celery@a8b6d9f571a0`) con dos réplicas.
- **`-c N` del comando Celery** (en el `command` del servicio `worker`) es
  la concurrencia *dentro* de cada réplica: con `--scale worker=2` y `-c 4`
  son 8 procesos Celery totales, no 2 ni 4 — PROJECT_STATEMENT §14 exige
  distinguir ambos números.

Ninguno de los dos simula varias **máquinas**: para eso hace falta un
worker externo apuntando al mismo Redis/MinIO (ver "Worker externo en otro
host" en [INSTALL.md](../INSTALL.md)). Para medir el efecto real de la
concurrencia sobre la duración de un análisis, ver
[VERIFICACION_BENCHMARK.md](VERIFICACION_BENCHMARK.md) (1 nodo, `-c
{1,2,4}`, mismo agregado en las nueve corridas).

## Dónde ver cada resultado

- **Bucket de MinIO**: la consola web en `http://localhost:9001`
  (publicada por `docker-compose.yml`) **no respondió** en esta
  verificación real (`Connection reset by peer`/`Connection refused`
  dentro del contenedor; solo el puerto `9000` de la API queda escuchando,
  aunque el proceso arranca con `--console-address :9001`) — la imagen
  `bitnamilegacy/minio:2025.7.23-debian-12-r5` no sirve el Object Browser
  en este build. Para inspeccionar el bucket sin la consola, usar `mc`
  (incluido en la imagen del servicio `minio`) o `boto3` desde el host:

  ```bash
  docker compose exec minio mc alias set local http://127.0.0.1:9000 minioadmin minioadmin
  docker compose exec minio mc ls local/<bucket>
  ```

  Verificado real: listó `access.log` con tamaño y fecha de subida.

- **SQLite del coordinador** (`/data/informes.sqlite` dentro del
  contenedor, issue #20): consulta directa sin salir del contenedor,
  ningún cliente externo necesita acceso al volumen:

  ```bash
  docker compose exec coordinador python -c "
  import sqlite3
  conn = sqlite3.connect('/data/informes.sqlite')
  for fila in conn.execute(
      'SELECT job_id, archivo, query, lineas, invalidas, workers FROM informes ORDER BY id DESC LIMIT 5'
  ):
      print(fila)
  "
  ```

  Verificado real: devolvió los cuatro informes de la sesión (uno por
  consulta), con `job_id`, archivo, consulta, líneas válidas/inválidas y
  cantidad de workers observados. El esquema completo (tablas `informes` y
  `resultados`) está en `indexador.py`.

- **Logs de cada servicio**: `docker compose logs <servicio> --tail N`.
  El coordinador registra admisión de jobs y arranque del indexador; cada
  worker registra `map_chunk job_id=... rango=[inicio,fin) query=...` por
  tramo recibido y el `Counter` parcial devuelto — útil para ver, sin tocar
  código, qué tramo exacto tocó a qué réplica.

## Cómo lee el archivo cada worker

El coordinador **nunca lee contenido**: pide el tamaño con `tamano()` y
calcula los cortes `[i, min(i+C, S))` por aritmética (PROJECT_STATEMENT
§8.1); encola `(uri, inicio, fin)` por Redis, no bytes. Cada worker resuelve
su propio acceso a través de `lector.py` (`local://` vía `mmap`, `s3://` vía
`GET Range` contra MinIO) y alinea los bordes de su tramo:

1. Si `inicio == 0`, arranca ahí.
2. Si `inicio > 0`, consulta el byte anterior: si es LF ya está alineado;
   si no, descarta la cola de la línea del tramo anterior hasta el
   siguiente LF.
3. Procesa cada línea cuyo primer byte cae en `[inicio, fin)` y completa la
   última hasta LF o EOF, aunque eso la lleve más allá de `fin` — nunca por
   conteo de líneas, siempre por posición de byte (invariante 3 de
   `CLAUDE.md`, [ADR-0005](adr/0005-particionado-y-lineas.md)).

Una línea pertenece al tramo que contiene **el offset de su primer byte**:
la lectura auxiliar puede solaparse entre tramos vecinos, pero la
*propiedad* de cada registro es disjunta y cubre el archivo completo. Esto
es lo que permite que `map_chunk` sea idempotente (invariante 6): si Celery
reintenta una tarea porque su worker murió a mitad de camino, el reintento
recalcula el mismo tramo `[inicio, fin)` desde cero y produce el mismo
`Counter`, sin depender de ningún estado dejado por el intento anterior —
por eso un worker caído no duplica ni pierde líneas (verificado real en
[VERIFICACION_FALLOS.md](VERIFICACION_FALLOS.md)).

## Problemas comunes al operar a mano

- **Puerto ocupado por un contenedor suelto de una sesión anterior**:
  `docker compose up` falla o un servicio no llega a `healthy` si `6379`,
  `8888`, `9000` o `9001` ya están tomados. Revisar con `docker ps` y
  detener el contenedor suelto (`loghunter-redis`/`loghunter-minio` de
  pruebas manuales previas, por ejemplo) antes de levantar Compose.
- **`BucketAlreadyOwnedByYou`** al repetir la creación del bucket:
  verificado real — `create_bucket` sobre un bucket que ya existe y ya es
  propio devuelve ese error, no lo recrea en silencio. No es necesario
  evitarlo por completo (es idempotente: el bucket sigue existiendo), pero
  conviene no leerlo como una falla de la infraestructura.
- **`docker compose down -v` borra los volúmenes**: `minio-data` y
  `sqlite-data` se pierden ahí, a diferencia de un `docker compose down`
  simple (sin `-v`), que preserva el bucket y el histórico de SQLite entre
  recreaciones del stack (verificado en
  [INSTALL.md](../INSTALL.md#datos-persistentes-al-recrear)). Usar `-v`
  solo cuando se quiere empezar de cero a propósito.

## Referencias

[GUIA_DEMO.md](GUIA_DEMO.md), [INSTALL.md](../INSTALL.md),
PROJECT_STATEMENT §7/§8/§12, [ADR-0005](adr/0005-particionado-y-lineas.md),
invariantes 3 y 6 de `CLAUDE.md`, [VERIFICACION_BENCHMARK.md](VERIFICACION_BENCHMARK.md).

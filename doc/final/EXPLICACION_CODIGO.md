# LogHunter — explicación completa del código, el flujo y los conceptos

Documento de estudio para la defensa final. Acompaña a
[OPERACION.md](OPERACION.md) (cómo ejecutar cada cosa) y explica lo otro:
**qué hace cada archivo, en qué orden entra en juego, y dónde exactamente
vive cada concepto que la cátedra evalúa** (concurrencia, paralelismo,
sockets, IPC, señales, colas distribuidas).

El orden de los archivos **no** es el de las carpetas: es el orden del
flujo de ejecución real, desde que se genera un log hasta que el informe
queda guardado en SQLite.

---

## Parte 0 — Conceptos antes de abrir un archivo

### 0.1 Qué es LogHunter en una frase

Un analizador **distribuido** de logs web (formato *combined* de
Apache/Nginx): un cliente pide por TCP "dame las 10 IPs que más
aparecen en este log de 2 GB", y un coordinador parte el archivo en
tramos de bytes, los reparte entre N procesos worker que pueden estar en
otras máquinas, suma los resultados parciales y devuelve un informe
único.

Es un **map-reduce** hecho a mano:

- **map**: cada worker procesa un tramo `[inicio, fin)` del archivo y
  devuelve un `Counter` (diccionario clave → cantidad).
- **reduce**: el coordinador suma todos los `Counter` parciales en uno
  total, ordena y recorta al `--top` pedido.

### 0.2 ¿Logs de qué? El dato de entrada, explicado

La pregunta aparece sola en cuanto se ve una salida con IPs: *"¿logs de
qué, de quién es esa IP?"*. La respuesta corta:

> Son los **logs de acceso de un servidor web / API** —el `access.log`
> que escriben Apache y Nginx—. **Cada línea es una petición HTTP
> atendida**: quién la pidió, cuándo, qué pidió, qué le respondió el
> servidor y cuántos bytes se le enviaron.

No es un log de errores ni de aplicación: es el registro de tráfico. Un
servidor con carga real escribe **millones de líneas por día**, y de ahí
sale el archivo de GB que justifica todo el proyecto.

#### Anatomía de una línea

El formato es **combined**, el default de Nginx y Apache (`LogFormat
combined`). Esta es una línea real generada por `generar.py`:

```
8.199.221.156 - - [01/Jan/2026:00:00:00 -0300] "POST /login HTTP/1.1" 500 7689 "-" "loghunter-generar/1.0"
```

Desplegada, campo por campo:

```
8.199.221.156                 ← IP del CLIENTE que hizo la petición
-                             ← identd (en desuso, siempre "-")
-                             ← usuario autenticado (vacío si no hay login HTTP)
[01/Jan/2026:00:00:00 -0300]  ← cuándo se atendió, con offset de zona
"POST /login HTTP/1.1"        ← método + ruta pedida + versión del protocolo
500                           ← código de respuesta HTTP
7689                          ← bytes del cuerpo de la respuesta
"-"                           ← referer: de dónde venía (acá, sin referer)
"loghunter-generar/1.0"       ← user-agent: con qué cliente llamó
```

| Campo | Qué es | Quién lo pone |
|---|---|---|
| **IP** | Dirección del **cliente** que hizo la petición | El servidor la lee del socket TCP |
| `-` `-` | `identd` y usuario autenticado; casi siempre vacíos | — |
| **timestamp** | Cuándo se atendió, con offset de zona | El servidor |
| **método** | `GET`, `POST`, `PUT`, `DELETE` | El cliente |
| **ruta** | Qué recurso pidió: `/api/users`, `/login` | El cliente |
| **estado** | Código de respuesta HTTP: `200`, `404`, `500` | El servidor |
| **bytes** | Tamaño del cuerpo de la respuesta | El servidor |
| **referer** / **user-agent** | De dónde venía y con qué cliente | El cliente |

**La IP es del cliente, no del servidor.** Es la dirección desde la que
entró la petición: un navegador, un celular, otro servicio que consume
la API, o un bot. El servidor la registra porque es lo único que
identifica a quien llamó. Por eso `top-ips` responde *"¿quién me está
pegando más?"*.

#### Cada consulta lee un campo distinto

Las cuatro consultas del proyecto no son arbitrarias: son las preguntas
clásicas que se le hacen a un `access.log`, una por campo.

| Consulta | Campo que mira | Pregunta operativa que responde |
|---|---|---|
| `top-ips` | IP del cliente | ¿Quién consume más? ¿Hay abuso, un scraper o un script mal hecho? |
| `urls` | Ruta pedida | ¿Qué endpoints se usan de verdad? ¿Qué conviene cachear u optimizar? |
| `status` | Código HTTP | ¿Está sano el servicio? Muchos `500` = algo se rompe; muchos `404` = enlaces o clientes desactualizados |
| `traffic` | Bytes, agrupados por hora | ¿Cuándo son los picos de carga? ¿Cuánto ancho de banda consume? |

Ojo con `status`: **no mide tamaños, cuenta ocurrencias por código**. Que
el log tenga 9526 respuestas `500` significa 9526 peticiones fallidas,
no bytes.

#### Por qué este dato de entrada y no otro

Un `access.log` es el caso de uso canónico de procesamiento distribuido,
y tiene tres propiedades que lo hacen ideal para lo que la materia
evalúa:

1. **Es enorme y crece sin techo.** Justifica no cargarlo en memoria y
   partirlo por rangos de bytes.
2. **Está orientado a líneas y cada línea es independiente.** No hay
   estado entre registros, así que se puede partir en cualquier punto
   —cuidando los bordes— y procesar los tramos en paralelo sin
   coordinación entre workers. Es lo que habilita el map-reduce.
3. **El cómputo es CPU-bound.** Aplicar una expresión regular a millones
   de líneas quema CPU, no espera I/O: es exactamente el caso donde el
   GIL obliga a usar **procesos** y no hilos (ver 0.3 y 0.4).

Dicho de otra forma: **el log es la excusa**. Lo que se demuestra con él
son sockets concurrentes, IPC, asincronismo y colas distribuidas. Pero es
una excusa realista — cualquier equipo de infraestructura corre estas
mismas cuatro consultas sobre sus logs todas las semanas.

#### El log del proyecto es sintético, y eso es a propósito

`generar.py` (Parte 2, archivo 1) fabrica un `access.log` combined
válido con tamaño, cantidad de IPs y porcentaje de líneas corruptas
controlados, y **semilla fija**. Tres razones:

- **Reproducibilidad.** Con `--seed 42` se obtiene el mismo archivo byte
  a byte, así que los números de la demo no cambian entre corridas.
- **Verdad de referencia.** El generador informa cuántas líneas válidas
  e inválidas escribió, y toda consulta sobre ese archivo tiene que
  devolver exactamente eso. Es lo que permite afirmar que el
  particionado no pierde ni duplica registros.
- **Líneas corruptas a pedido.** `--corrupt-pct` inyecta registros
  inválidos (IP mala, fecha inválida, texto suelto) para demostrar que
  una línea mal formada se cuenta aparte y **no rompe el job**, que es
  justo lo que pasa con logs reales.

En producción el archivo vendría del servidor web; para el sistema es
indistinto, porque lo único que necesita es un archivo de texto con una
petición por línea en formato combined.
### 0.3 Concurrencia vs paralelismo (la pregunta clásica del tribunal)

| | Concurrencia | Paralelismo |
|---|---|---|
| Qué es | Varias tareas **en progreso** a la vez, intercaladas | Varias tareas **ejecutándose** literalmente al mismo instante |
| Hace falta | Un solo núcleo alcanza | Varios núcleos / CPUs |
| Sirve para | Esperar muchas cosas a la vez (I/O) | Calcular más rápido (CPU) |
| En este proyecto | `asyncio` en `coordinador.py` | Procesos Celery en `tasks.py`/`workers.py` |

La frase corta para la defensa: **concurrencia es cómo organizo las
esperas; paralelismo es cómo multiplico el cómputo.** Son problemas
distintos y por eso el proyecto usa dos mecanismos distintos a propósito.

### 0.4 Por qué dos modelos de concurrencia conviven

El punto conceptual central del proyecto:

- El **coordinador es I/O-bound**: casi todo su tiempo lo pasa
  *esperando* — esperando que un cliente mande un pedido, esperando que
  Redis entregue resultados, esperando que el socket se vacíe. No
  calcula nada pesado. Para esperar muchas cosas a la vez, `asyncio` es
  el mecanismo más barato: **un solo hilo, un solo proceso**, sin costo
  de crear hilos ni procesos y — clave — **sin *data races***, porque
  dentro de un event loop solo corre una corrutina a la vez.
- Los **workers son CPU-bound**: aplican expresiones regulares sobre
  millones de líneas. Eso es cálculo puro. En CPython el **GIL** (Global
  Interpreter Lock) impide que dos hilos ejecuten bytecode Python
  simultáneamente, así que usar hilos **no** daría paralelismo real de
  cálculo. La única forma de usar los N núcleos es **procesos
  separados** — que es exactamente lo que hace el pool `prefork` de
  Celery.

> Si te preguntan "¿por qué no hilos en los workers?": por el GIL. Si
> preguntan "¿por qué no procesos en el coordinador?": porque no tiene
> nada que calcular, solo esperas, y un proceso por cliente costaría
> memoria y sincronización sin ganar nada.

### 0.5 Las cuatro formas de "hacer varias cosas a la vez" que aparecen acá

El proyecto toca deliberadamente los cuatro mecanismos que la materia
evalúa:

1. **Asincronismo de I/O** (`asyncio`) → `coordinador.py`. Sockets
   concurrentes, corrutinas, `await`, `asyncio.Lock`,
   `asyncio.Semaphore`, `asyncio.to_thread`.
2. **Paralelismo real por procesos** (Celery `prefork` + Redis) →
   `tasks.py` + `workers.py`. Procesos independientes, posiblemente en
   otras máquinas.
3. **IPC local entre procesos** (`multiprocessing`) → `ipc.py` +
   `indexador.py`. `Queue` para pasar informes, `Value` + `Lock` para
   contadores compartidos.
4. **Señales POSIX** (`signal`) → `coordinador.py` + `indexador.py`.
   `SIGINT`/`SIGTERM` para apagado ordenado, `SIGUSR1` para pedir un
   snapshot de estado sin reiniciar nada.

Y además **hilos**, pero en un rol muy acotado: `asyncio.to_thread`
saca del event loop las llamadas bloqueantes que no tienen versión
asíncrona (Celery, `boto3`, `sqlite3`, `Queue.put`).

### 0.6 Tecnologías y por qué cada una

| Tecnología | Para qué se usa | Por qué esa y no otra |
|---|---|---|
| **`asyncio`** (stdlib) | Servidor TCP del coordinador | I/O-bound puro; un hilo atiende N clientes sin *data races* (ADR-0002) |
| **Celery** | Cola de tareas distribuida | Da broker, reentrega tras caída de worker y ejecución en otros hosts; `multiprocessing.Pool` solo da procesos locales (ADR-0003) |
| **Redis** | Broker (cola) + backend (resultados) | Un solo servicio cubre las dos funciones; RabbitMQ habría requerido un segundo almacén para resultados |
| **MinIO** (API S3) | Almacenamiento del log accesible por red | Permite `GET Range`: leer solo el tramo que a cada worker le toca, sin bajar el archivo entero (ADR-0004) |
| **`boto3`** | Cliente S3 | Cliente oficial de la API S3, hablado por MinIO |
| **`mmap`** (stdlib) | Backend local de lectura | Mapea el archivo en memoria virtual; el kernel cachea páginas, las relecturas solapadas son casi gratis |
| **`multiprocessing`** | IPC coordinador ↔ indexador | `Queue` y `Value`+`Lock` son el mecanismo IPC canónico de la stdlib (ADR-0009) |
| **SQLite** | Histórico de informes | Sin servidor que administrar; viable **porque hay un único escritor** (el indexador) (ADR-0008) |
| **JSON sobre TCP** | Protocolo cliente ↔ coordinador | `pickle` **ejecuta código al deserializar** y el coordinador acepta conexiones de cualquiera: sería un agujero de seguridad (ADR-0006) |
| **Docker Compose** | Despliegue reproducible | Levanta Redis, MinIO, coordinador y N workers con un comando |
| **pytest** + **uv** | Pruebas y entorno | 217 tests; `uv sync --locked` fija versiones exactas |

### 0.7 La arquitectura de un vistazo

```
cliente.py  ──TCP:8888──►  coordinador.py  ──Celery/Redis──►  workers (map_chunk)
 (CLI)        JSON/\n        (asyncio)         coordenadas        (N procesos/máquinas)
                                 │                                  │
                      multiprocessing.Queue              lector.py  │
                                 ▼                     ┌────────────┴───────────┐
                           indexador.py ──► SQLite     │ local:// → mmap        │
                           (proceso hijo)              │ s3://    → GET Range   │
                                                       └────────────┬───────────┘
                                                                    ▼
                                                        MinIO (API S3) :9000
```

Lo que **viaja** por cada flecha importa:

- cliente → coordinador: **un pedido JSON** (`{file, query, top}`), nunca el archivo.
- coordinador → Redis → worker: **coordenadas** `(uri, inicio, fin, query, job_id)`, **nunca bytes del log**. Mandar los datos sería empujar GB por Redis.
- worker → almacenamiento: el worker resuelve **él mismo** el acceso, así que nada lo ata a la máquina del archivo.
- worker → Redis → coordinador: un `Counter` parcial (pocos KB).
- coordinador → indexador: el informe completo por `multiprocessing.Queue`.

### 0.8 Los nueve invariantes de diseño

Están en `CLAUDE.md` y son las reglas que, si se violan, rompen la
justificación del proyecto. Vale saberlas porque cada una tiene su lugar
exacto en el código:

1. **Nunca bloquear el event loop** → todo lo bloqueante va con `asyncio.to_thread`.
2. **Por la cola viajan coordenadas, no datos.**
3. **El coordinador no lee el contenido del log** — solo pide el tamaño.
4. **Todo acceso a un `Value` compartido va bajo `Lock`** (`+=` es read-modify-write, no es atómico).
5. **Ninguna sección crítica cede control sin sincronización** — el patrón *leer → `await` → escribir* está prohibido.
6. **`map_chunk` debe ser idempotente**, porque Celery reintenta tareas de workers caídos.
7. **El indexador es el único escritor de SQLite.**
8. **Cada job tiene su `job_id` y su grupo de resultados** — los parciales nunca se mezclan.
9. **El protocolo es JSON delimitado por `\n`, no `pickle`.**

---

## Parte 1 — Estructura de carpetas

```
final/
├── README.md               Portada: estado, documentación, uso básico
├── INSTALL.md              Entorno, servicios, Compose, workers remotos
├── INFO.md                 Decisiones de diseño y su justificación
├── TODO.md                 Mejoras y features futuras
├── CLAUDE.md / AGENTS.md   Instrucciones del repo para agentes
├── pyproject.toml          Paquete, dependencias, config de pytest
├── uv.lock                 Versiones exactas (reproducibilidad)
├── Dockerfile              Imagen única: el rol lo decide el Compose
├── docker-compose.yml      Redis + MinIO + coordinador + N workers
│
├── src/loghunter/          EL CÓDIGO (12 módulos + __init__ vacío)
│   ├── __init__.py         Vacío a propósito: importar no inicializa nada
│   ├── generar.py          Genera logs sintéticos reproducibles
│   ├── parser.py           Interpreta una línea combined → Registro
│   ├── consultas.py        Agregadores de las 4 consultas + reduce
│   ├── lector.py           Lectura por rangos: local:// (mmap) y s3:// (GET Range)
│   ├── workers.py          Núcleo map-reduce: calcular_rangos + map_chunk
│   ├── tasks.py            App Celery y la tarea distribuida map_chunk
│   ├── analisis.py         Lanza el grupo de tareas y reduce sus parciales
│   ├── protocolo.py        Protocolo JSON/LF: framing, validación, mensajes
│   ├── coordinador.py      Servidor asyncio: admisión, planificación, reduce, señales
│   ├── cliente.py          CLI que pide el análisis y muestra el informe
│   ├── ipc.py              Contadores compartidos (Value + Lock)
│   └── indexador.py        Proceso hijo: único escritor de SQLite
│
├── tests/                  20 archivos, 217 tests
├── scripts/
│   └── benchmark_procesos.py   Medición real con 1, 2 y 4 procesos
│
└── doc/
    ├── final/              ← ESTA CARPETA (lo que hay que leer antes del final)
    │   ├── EXPLICACION_CODIGO.md   Este archivo
    │   └── OPERACION.md            Cómo levantar, ejecutar y probar cada comando
    └── adr/                    15 decisiones de arquitectura (ADR-0001…0015)
```

**Dato importante para la defensa:** `src/loghunter/__init__.py`
contiene solo un docstring. Es deliberado: importar el paquete no abre
sockets, no conecta a Redis ni crea procesos. Hay un test
(`test_imports.py`) que lo verifica prohibiendo esas llamadas.

---

## Parte 2 — Archivo por archivo, en orden de ejecución

El recorrido va **bajando** desde el pedido del usuario hasta los bytes
del archivo, y después **subiendo** otra vez hasta el informe guardado:

```
 1. generar.py      preparar el dato
 2. cliente.py      el usuario pide
 3. protocolo.py    el pedido se valida
 4. coordinador.py  se admite, se planifica, se reparte      ← el cerebro
 5. lector.py       cómo se llega a los bytes
 6. workers.py      cómo se corta y se procesa un tramo       ← el map
 7. parser.py       cómo se interpreta una línea
 8. consultas.py    cómo se agrega y se reduce                ← el reduce
 9. tasks.py        cómo eso se vuelve una tarea distribuida
10. analisis.py     cómo se lanza el grupo y se cosecha
11. ipc.py          contadores compartidos entre procesos
12. indexador.py    el informe se persiste
```

---

### 1. `src/loghunter/generar.py` — generador de logs sintéticos

**Qué es:** una CLI independiente (`python -m loghunter.generar`) que
fabrica un log *combined* del tamaño que pidas. No forma parte del
análisis: es la herramienta para tener un archivo de 2 GB con el que
demostrar que el sistema escala.

**Qué hace y por qué así:**

- **Genera por bloques, nunca en memoria.** `generar_log()` devuelve un
  **generador** de líneas (`Iterator[bytes]`), no una lista. Un archivo
  de 2 GB nunca existe completo en RAM. Esto es relevante para la
  defensa: la misma disciplina de "procesar sin cargar todo" que después
  aplican los workers.
- **Es reproducible.** `random.Random(seed)` es un PRNG **local**
  (no el global de `random`), y la fecha base está fija
  (`_FECHA_BASE = 2026-01-01`). Misma semilla + mismos argumentos =
  **exactamente los mismos bytes**. Sin eso no se puede comparar un
  resultado distribuido contra una referencia secuencial, ni repetir un
  benchmark.
- **Inyecta líneas corruptas a propósito.** `--corrupt-pct` mezcla
  `_linea_corrupta()` (IP inválida, fecha inválida, texto suelto) para
  probar que un registro malo no aborta nada, solo se cuenta como
  inválido.
- **Simula horas pico.** `_HORAS_PICO = {9, 10, 20, 21}` acelera el paso
  del timestamp en esas horas, para que la consulta `traffic` muestre
  picos reconocibles.

**Funciones destacables:**

| Función | Qué hace |
|---|---|
| `generar_log(...)` | Valida argumentos **y después** devuelve el generador (así un `--size-mb -5` falla ya, no en el primer `next()`) |
| `escribir_log(destino=...)` | Mira el esquema del destino y despacha a local o S3 |
| `_escribir_log_local()` | Abre el archivo y escribe línea por línea, contando válidas/inválidas con `parsear_linea` al vuelo |
| `_escribir_log_s3()` | Sube a MinIO con `upload_fileobj` |
| `_FlujoGenerado` | **Clase interesante:** adapta un *generador* a la interfaz `.read(size)` que `upload_fileobj` espera. En el peor caso el buffer tiene una línea de más que lo ya devuelto: nunca junta el archivo en memoria. Cuenta tamaño/válidas/inválidas en la misma pasada en que sube los bytes, sin regenerar el stream. |
| `_linea_valida()` / `_formatear_timestamp()` | Arman el texto combined exacto, con offset horario `-0300` |

**Tests (`tests/test_generar.py`, 13 tests):** que `--size-mb 0` produce
archivo vacío; que **la misma semilla produce los mismos bytes** y
semillas distintas bytes distintos; que alcanza o supera el tamaño
objetivo completando la última línea; que **todo lo generado es
parseable** por `parser.py`; que los timestamps son monótonos; que los
argumentos fuera de rango se rechazan; que la subida a S3 produce
**los mismos bytes** que la escritura local; que `_FlujoGenerado`
devuelve lo mismo sea cual sea el tamaño de bloque pedido.

---

### 2. `src/loghunter/cliente.py` — la CLI que dispara el análisis

**Qué es:** el punto de entrada del usuario.
`python -m loghunter.cliente -f s3://logs/access.log -q top-ips --top 10`.

**📌 Concepto: acá NO hay concurrencia, y es una decisión consciente.**
Un socket **bloqueante** (`socket.create_connection`) alcanza para un
pedido por ejecución. Poner `asyncio` en el cliente sería complejidad sin
beneficio: no tiene nada que hacer mientras espera. Si te preguntan por
qué el cliente es sincrónico y el coordinador no: *el cliente espera una
cosa, el coordinador espera N*.

**Qué hace, paso a paso (`main`):**

1. `construir_parser()` arma el `argparse` con los flags y sus defaults
   (`-i 127.0.0.1`, `-p 8888`, `--top 10`, `--chunk-mb` sin default
   propio). `choices=protocolo.CONSULTAS` hace que una consulta inválida
   muera en el cliente con código 2, sin molestar al servidor.
2. `_normalizar_archivo()` convierte una ruta relativa a
   `local:///ruta/absoluta`. **Ojo con esto en la defensa:** normalizar
   la ruta **no sube el archivo**. El path tiene que existir en la
   máquina del *worker*, no en la del cliente.
3. `_construir_pedido()` arma el dict JSON. Omite `chunk_mb` si el
   usuario no lo dio, para que decida el coordinador (precedencia:
   flag del cliente > default del coordinador).
4. `socket.create_connection((ip, port), timeout=10)` — resuelve IPv4 o
   IPv6 según la dirección. Después hace `sock.settimeout(None)`: el
   timeout de 10 s era para **conectar**; el análisis puede tardar
   minutos y no debe morir por timeout de socket.
5. `sock.sendall(protocolo.codificar(pedido))` — un JSON + `\n`.
6. `_consumir_respuestas()` lee mensajes línea por línea con
   `sock.makefile("rb").readline()` hasta recibir uno **terminal**
   (`result` o `error`).

**`_consumir_respuestas` es la máquina de estados del cliente.** Según
el `type` del mensaje:

- `accepted` con `state: queued` → "Esperando turno" (el coordinador
  está al tope de jobs simultáneos).
- `started` → cuántos tramos se van a procesar.
- `progress` → reescribe la línea con `\r` (`3/64`, `7/64`…).
- `result` → imprime la tabla con `_mostrar_informe()` y **devuelve 0**.
- `error` → imprime `Error [CODIGO]: mensaje` en stderr y **devuelve 1**.

**El modo `--raw`:** stdout recibe **exactamente** el objeto JSON
terminal más un LF, y todo lo demás (progreso, diagnósticos) va a
stderr. Así se puede hacer `cliente ... --raw | jq .duracion`. Es lo
que usa `scripts/benchmark_procesos.py` para medir.

**Códigos de salida:** `0` éxito, `1` error del análisis o de conexión,
`2` argumentos inválidos (lo pone `argparse`).

**Tests (`tests/test_cliente.py`, 13 tests):** las funciones puras sin
red (normalización, construcción del pedido, rechazo de flags
inválidos, consumo de respuestas desde un `io.BytesIO`), y además
**recorridos reales por subproceso** contra un coordinador vivo:
código 0 y tabla en modo humano, `--raw` produce exactamente un JSON +
LF en stdout, archivo inexistente sale con 1, **funciona por IPv6**,
argumentos inválidos salen con 2.

---

### 3. `src/loghunter/protocolo.py` — el protocolo de aplicación

**Qué es:** la definición del idioma que hablan cliente y coordinador, y
la validación estricta de todo lo que entra. **No es el transporte**: no
hace `recv` ni `send`, solo *framing* y validación. Eso lo hace
testeable sin red.

**📌 Concepto 1: *framing*. ¿Por qué `\n`?**

TCP es un **stream de bytes**, no de mensajes: puede fragmentar un
mensaje en varios paquetes o pegar dos mensajes en una sola lectura. Un
`recv(1024)` **no** equivale a "un mensaje". Hay que delimitar. Acá el
delimitador es el salto de línea (LF), y por eso se lee siempre con
`readline()`, nunca con `read(n)`. Hay dos tests específicos de esto:
un mensaje partido en varios `feed_data` y dos mensajes juntos en uno
solo — ambos se interpretan bien.

**📌 Concepto 2: ¿por qué JSON y no `pickle`? (invariante 9)**

`pickle.loads()` **ejecuta código arbitrario al deserializar**. El
coordinador escucha en `0.0.0.0:8888` y acepta conexiones de
cualquiera. Aceptar `pickle` sería dar ejecución remota de código a
quien se conecte. JSON solo describe datos. Es el argumento de
seguridad del proyecto (ADR-0006) y conviene tenerlo listo.

**📌 Concepto 3: límites contra el agotamiento de recursos**

- `LIMITE_PEDIDO = 64 KiB`: un pedido más grande se rechaza.
- `TIMEOUT_PEDIDO = 30 s`: un cliente que conecta y no manda nada no
  retiene recursos para siempre.
- El `StreamReader` se construye con `limit=LIMITE_PEDIDO` (lo pasa
  `asyncio.start_server`), así una línea **sin** LF dentro del límite
  agota el buffer y lanza `LimitOverrunError` en vez de hacer crecer la
  memoria sin cota. Sin eso, un atacante mandando bytes sin `\n`
  llenaría la RAM del coordinador.
- El **informe final no comparte ese límite**: un `top-ips` puede ser
  grande. El límite es para lo que *entra*, no para lo que sale.

**Qué define el archivo:**

| Elemento | Qué es |
|---|---|
| `VERSION = 1` | Versionado del protocolo; un pedido con otra versión se rechaza |
| `CONSULTAS` | `("top-ips", "status", "urls", "traffic")` |
| `CODIGOS` | Los 12 códigos de error normativos (`INVALID_REQUEST`, `UNSUPPORTED_QUERY`, `UNSUPPORTED_URI`, `FILE_NOT_FOUND`, `FILE_ACCESS_ERROR`, `STORAGE_UNAVAILABLE`, `BROKER_UNAVAILABLE`, `TASK_FAILED`, `JOB_TIMEOUT`, `SERVICE_UNAVAILABLE`, `SERVER_SHUTDOWN`, `INTERNAL_ERROR`) |
| `ErrorProtocolo` | Excepción que lleva su código. `job_id=None` cuando el error precede a la creación del job (todo lo estructural) |
| `validar_uri()` | Rechaza esquemas no soportados y `.gz` (comprimidos están fuera de alcance: no se puede leer un rango de un gzip sin descomprimir todo lo anterior) |
| `validar_pedido()` | Validación estricta: rechaza campos desconocidos, campos faltantes, versión distinta, tipo distinto de `analyze`, consulta desconocida, `top` no positivo, `chunk_mb` fuera de 1–256. Normaliza devolviendo `top=10` por default |
| `codificar()` | `json.dumps(...) + "\n"` en UTF-8 |
| `leer_pedido()` | Lee **un** pedido delimitado por LF con timeout y límite, decodifica, parsea y valida |
| `msg_accepted/started/progress/result/error` | Constructores de los cinco tipos de mensaje de respuesta |

**Detalle fino que suele gustar en una defensa:**
`_es_entero_estricto()` existe porque **en Python `bool` es subclase de
`int`**: `isinstance(True, int)` es `True`. Sin ese chequeo, `{"top":
true}` pasaría la validación y se interpretaría como `top=1`. Hay un
test que lo cubre.

**El diálogo completo del protocolo:**

```
cliente → {"version":1,"type":"analyze","file":"s3://logs/a.log","query":"top-ips","top":10}
         ← {"type":"accepted","job_id":"ab12…","state":"admitted"}   (o "queued")
         ← {"type":"started","job_id":"ab12…","chunks":64,"chunk_mb":32}
         ← {"type":"progress","job_id":"ab12…","completed":7,"total":64}
         ← {"type":"progress", … }                                   (cada ~1 s)
         ← {"type":"result","job_id":"ab12…","resultados":[…], …}     TERMINAL
   o bien ← {"type":"error","job_id":"ab12…","code":"JOB_TIMEOUT", …} TERMINAL
```

**Tests (`tests/test_protocolo.py`, 16 tests):** framing fragmentado y
framing junto; límites de tamaño y de tiempo; pedido sin LF; conexión
cerrada sin datos; JSON malformado; JSON que no es objeto; y una tabla
parametrizada de variantes inválidas con su código esperado (versión
mala, tipo malo, consulta desconocida, URI no soportada, `.gz`, `top`
booleano, `chunk_mb` fuera de rango).

---

### 4. `src/loghunter/coordinador.py` — el servidor `asyncio` (el archivo central)

**Qué es:** el cerebro del sistema y **el archivo donde vive casi todo lo
que la materia evalúa de concurrencia**. 657 líneas. Un proceso, un
event loop, un hilo principal, más hilos auxiliares para lo bloqueante.

Tres responsabilidades, y una **no**-responsabilidad:

- ✅ Atender N clientes TCP concurrentes.
- ✅ Planificar: pedir el **tamaño** del archivo y calcular los cortes por aritmética.
- ✅ Reducir los parciales, presentar el informe y encolarlo para persistencia.
- ❌ **No cuenta nada y no lee el contenido del log** (invariante 3).

#### 4.1 Arranque (`main` → `_servir`)

```python
main()                        # parsea flags, exporta CELERY_BROKER_URL / S3_ENDPOINT
  └─ asyncio.run(_servir(...))
       ├─ asyncio.start_server(coordinador.atender_cliente, host, port,
       │                       limit=protocolo.LIMITE_PEDIDO)
       ├─ coordinador.iniciar_servicios(servidor, db_path)
       │     ├─ _iniciar_indexador(db_path)   → multiprocessing.Process
       │     ├─ _supervisar_indexador()       → tarea de fondo
       │     └─ loop.add_signal_handler(SIGINT / SIGTERM / SIGUSR1)
       └─ await coordinador.esperar_cierre()  → bloquea hasta que una señal apague
```

Un detalle deliberado: **`--broker` y `--s3-endpoint` se exportan como
variables de entorno *antes* de que se importe `loghunter.analisis`**.
`_cargar_analisis()` hace el import **tardío**, dentro de la función, por
dos motivos: (a) importar `coordinador` no arrastra Celery/Redis, y (b)
cuando `tasks.crear_app()` finalmente lea el entorno, los flags de la CLI
ya están puestos. Si el import fuera arriba del archivo, `--broker` no
tendría efecto.

#### 4.2 El `dataclass Job` — el estado de un pedido

```python
@dataclass(slots=True)
class Job:
    id, file, query, top, chunk_mb
    estado: str = "recibido"     # recibido→queued→running→completed/failed/timed_out/cancelled
    grupo: Any = None            # el Grupo de AsyncResult de Celery
    completados: int = 0         # tramos exitosos hasta ahora
    total: int = 0               # tramos del job
    admitido_en, ts_utc, deadline
```

**Punto fino de concurrencia:** dentro de un mismo job, el `Job`
**pertenece exclusivamente a su propia tarea** (`_procesar_job`), así que
no necesita lock propio. Lo que sí necesita lock es el **diccionario**
`self._jobs`, porque lo toca cualquier conexión.

#### 4.3 `atender_cliente` — una corrutina por conexión

Es el *callback* que `asyncio.start_server` invoca por cada cliente.
Esto es el **socket concurrente**: N clientes = N corrutinas en el mismo
hilo, intercaladas en cada `await`.

```python
pedido = await protocolo.leer_pedido(reader)   # error → msg_error y chau, sin crear job
job = Job(id=_nuevo_job_id(), ...)             # uuid4().hex

procesar    = asyncio.ensure_future(self._procesar_job(job, writer))
async with self._lock:
    self._conexiones[job.id] = (job, writer, procesar)

desconexion = asyncio.ensure_future(self._esperar_desconexion(reader))
terminados, _ = await asyncio.wait({procesar, desconexion},
                                   return_when=asyncio.FIRST_COMPLETED)
```

**📌 Concepto: la carrera deliberada de dos tareas.**
`asyncio.wait(..., FIRST_COMPLETED)` pone a competir "terminar el
análisis" contra "el cliente se desconectó". Gana la que termina
primero:

- Si gana `procesar` → se cancela la vigilancia de desconexión.
- Si gana `desconexion` → **se cancela el procesamiento**, lo que
  dispara el `except asyncio.CancelledError` de `_procesar_job`, que
  **revoca las tareas Celery pendientes** de ese job y libera su cupo.

Esto es el caso de uso CU-22: si el usuario hace Ctrl-C en el cliente,
el coordinador no sigue quemando workers en un resultado que nadie va a
leer, **y no afecta a los demás clientes**.

`_esperar_desconexion()` es simplemente `await reader.read(1)`: el
protocolo admite un pedido por conexión, así que cualquier byte extra o
un cierre (que devuelve `b""`) significan "ya no hay nadie esperando".

El `finally` limpia siempre: saca el job de `self._jobs` y
`self._conexiones` bajo lock, cierra el `writer` y espera su cierre
tolerando `ConnectionError`.

#### 4.4 `_procesar_job` — admisión, ciclo de vida y limpieza

**📌 Concepto: control de admisión con `asyncio.Semaphore` (invariante 5)**

```python
self._semaforo = asyncio.Semaphore(max_jobs)   # default: 3
...
estado_inicial = "queued" if self._semaforo.locked() else "admitted"
await self._enviar(writer, protocolo.msg_accepted(job.id, estado_inicial))

await self._semaforo.acquire()        # ← acá espera si no hay cupo
job.estado = "running"
job.admitido_en = time.monotonic()
job.deadline = job.admitido_en + self.job_timeout
```

Por qué un semáforo y no un contador con `if`: el patrón
*leer → `await` → escribir* es exactamente la carrera lógica que
`asyncio` **no** evita. `asyncio` elimina las *data races* (nunca hay
dos corrutinas escribiendo a la vez), pero **no** las carreras lógicas
que cruzan un punto de cesión: dos corrutinas pueden leer
`jobs_activos == 2`, ambas ceder en un `await`, y ambas concluir que hay
cupo. El `Semaphore` hace la verificación y la toma de cupo en una sola
operación atómica respecto del loop.

Otro detalle: un pedido sin cupo recibe `accepted` con `state: queued`
y **espera sin publicar tareas todavía**. No se ocupa Redis con trabajo
que todavía no tiene turno. Y **el deadline del job arranca al adquirir
el cupo**, no al conectar: el tiempo esperando en cola no cuenta contra
el `--job-timeout` (ADR-0007).

**Dos rechazos previos a la admisión**, que no consumen cupo:

- `self._cerrando` → `SERVER_SHUTDOWN` (ya llegó una señal de apagado).
- `not self._servicio_disponible` → `SERVICE_UNAVAILABLE` (el indexador
  se cayó; no se admite trabajo cuyo informe no se va a poder guardar).

**El `finally` es el punto crítico de corrección:**

```python
finally:
    self._metricas.decrementar_jobs_activos()
    self._semaforo.release()
```

Pase lo que pase — éxito, error, timeout, cancelación — **el cupo se
libera**. Si esto faltara, un job que falla dejaría el semáforo tomado
para siempre y el servidor se iría degradando hasta no admitir nada.

**La jerarquía de `except` también importa:**

- `except protocolo.ErrorProtocolo` → se manda el `msg_error` con su código, estado `failed` (o `timed_out` si el código es `JOB_TIMEOUT`).
- `except asyncio.CancelledError: raise` → **se re-lanza**. Nunca hay que tragarse una cancelación: romper el protocolo de cancelación de `asyncio` deja tareas zombi.
- `except Exception` (frontera de proceso) → se loguea con traceback y se responde `INTERNAL_ERROR`. **El servidor nunca se cae por un job.**
- El `except asyncio.CancelledError` externo revoca el grupo y re-lanza.

#### 4.5 `_ejecutar_analisis` — planificar sin leer

```python
chunk_bytes = job.chunk_mb * 2**20
tamano = await asyncio.to_thread(_tamano_archivo, job.file)   # ← solo el TAMAÑO
rangos = calcular_rangos(tamano, chunk_bytes)                 # ← pura aritmética
job.total = len(rangos)
await self._enviar(writer, protocolo.msg_started(job.id, job.total, job.chunk_mb))
```

**📌 Concepto: `asyncio.to_thread` y el invariante 1 (nunca bloquear el loop)**

`_tamano_archivo()` abre un `LectorLocal` (que hace `os.open` + `fstat`)
o un `LectorS3` (que hace un `HEAD` HTTP contra MinIO). Las dos cosas
son **I/O sincrónico bloqueante**, y `boto3` no tiene versión async. Si
se llamara directo, el `HEAD` HTTP congelaría el event loop y **los
demás clientes dejarían de ser atendidos** — ni siquiera recibirían su
`accepted`. `asyncio.to_thread` lo corre en un hilo del *executor*
default y devuelve un awaitable: el loop sigue atendiendo mientras el
hilo espera.

Los tres lugares donde esto se aplica en el archivo:

| Llamada bloqueante | Por qué bloquea | Envoltorio |
|---|---|---|
| `_tamano_archivo` | `fstat` / `HEAD` HTTP | `await asyncio.to_thread(...)` |
| `lanzar_grupo` (Celery `apply_async`) | escribe en Redis | `await asyncio.to_thread(...)` |
| `reducir_resultados` (sondeo de `AsyncResult`) | lee Redis en bucle con `sleep` | `await asyncio.to_thread(...)` |
| `_contar_completados` | lee Redis | `await asyncio.to_thread(...)` |
| `cola_informes.put` | `multiprocessing.Queue` puede bloquear si está llena | `await asyncio.to_thread(...)` |
| `proceso_indexador.join` | espera a un proceso | `await asyncio.to_thread(...)` |
| `_revocar_grupo` | escribe en Redis | `await asyncio.to_thread(...)` |

**Caso borde elegante:** si `tamano == 0`, `rangos` queda vacío, **no se
publica ninguna tarea**, no se toca Celery ni Redis, y se responde un
informe vacío válido. Un archivo vacío es el único caso en que el
sistema resuelve un job sin workers.

Después del reduce:

```python
self._metricas.sumar_lineas_validas(reducido["lineas"])   # una vez por job
duracion = time.monotonic() - job.admitido_en
contador_total = Counter(reducido["contador"])
top = job.top if job.query in ("top-ips", "urls") else None   # ← status/traffic no se recortan
resultados = presentar(job.query, contador_total, top)
persistencia = await self._persistir_informe(job, tamano, reducido, contador_total, duracion)
return protocolo.msg_result(job.id, ...)
```

`time.monotonic()` y no `time.time()`: el reloj monótono no salta si
alguien ajusta la hora del sistema o entra un cambio de horario. Para
medir **duraciones** siempre monotónico; `time.time()` se usa aparte
(`job.ts_utc`) solo para guardar *cuándo* ocurrió el informe.

#### 4.6 `_esperar_grupo` — progreso concurrente con el reduce

Es el patrón más lindo del archivo: **emitir progreso al cliente
mientras el reduce bloqueante corre en un hilo.**

```python
fin = asyncio.Event()

async def _progreso():
    anterior = -1
    while not fin.is_set():
        completados = await asyncio.to_thread(_contar_completados, job.grupo)
        if completados != anterior:
            await self._enviar(writer, protocolo.msg_progress(job.id, completados, job.total))
            anterior = completados
        try:
            await asyncio.wait_for(fin.wait(), timeout=1.0)   # ← espera 1 s O el fin, lo que llegue primero
        except asyncio.TimeoutError:
            pass

tarea_progreso = asyncio.ensure_future(_progreso())
try:
    return await asyncio.to_thread(self._reducir_resultados, job.grupo, restante, 0.5)
finally:
    fin.set()
    await tarea_progreso
```

Tres cosas para notar:

1. El `asyncio.wait_for(fin.wait(), timeout=1.0)` en vez de
   `asyncio.sleep(1)`: así, cuando el reduce termina, la tarea de
   progreso **despierta inmediatamente** en vez de dormir hasta un
   segundo de más.
2. `if completados != anterior` evita spamear mensajes idénticos.
   El progreso es **monótono** porque `_contar_completados` cuenta
   `ready() and successful()`.
3. El `finally` con `fin.set()` + `await tarea_progreso` garantiza que
   la tarea de progreso termina **siempre**, incluso si el reduce
   explota. Nada de tareas huérfanas.

El `restante = job.deadline - time.monotonic()` es lo que hace que el
`--job-timeout` sea un **deadline por job**: si vence, el reduce lanza
`TimeoutError`, se revoca el grupo de **ese** job y se responde
`JOB_TIMEOUT`, **sin tocar los demás clientes** (AC-11).

#### 4.7 Mapa de errores → códigos

Esta tabla es muy probable en una defensa ("¿y si se cae Redis?"):

| Qué falla | Dónde se detecta | Código al cliente |
|---|---|---|
| Pedido mal formado / versión / consulta / URI | `protocolo.leer_pedido` | `INVALID_REQUEST`, `UNSUPPORTED_QUERY`, `UNSUPPORTED_URI` |
| El archivo local no existe | `_tamano_archivo` → `FileNotFoundError` | `FILE_NOT_FOUND` |
| Sin permisos | `PermissionError` | `FILE_ACCESS_ERROR` |
| MinIO caído / credenciales ausentes | `AlmacenamientoNoDisponible` | `STORAGE_UNAVAILABLE` |
| Redis caído al publicar | `lanzar_grupo` falla | `BROKER_UNAVAILABLE` |
| Un tramo falló definitivamente | `TareaFallida` | `TASK_FAILED` (y se revoca el resto) |
| Venció el `--job-timeout` | `TimeoutError` | `JOB_TIMEOUT` (y se revoca el resto) |
| Indexador caído | supervisor / `_persistir_informe` | `SERVICE_UNAVAILABLE` |
| Llegó `SIGTERM`/`SIGINT` | `_apagar` | `SERVER_SHUTDOWN` |
| Bug inesperado | `except Exception` | `INTERNAL_ERROR` |

Patrón común a `TASK_FAILED`, `JOB_TIMEOUT` y `BROKER_UNAVAILABLE`:
**antes de responder el error se revoca el grupo**
(`await asyncio.to_thread(_revocar_grupo, job.grupo)`). `_revocar_grupo`
recorre `grupo.results` y llama `resultado.revoke()` **tarea por
tarea**: nunca mata workers globales, así que los tramos de *otros*
jobs siguen corriendo. Eso es el invariante 8 en acción.

#### 4.8 Persistencia: `_persistir_informe`

```python
if self._cola_informes is None:      return "no_configurada"   # modo de prueba
if not self._servicio_disponible:    raise ErrorProtocolo("SERVICE_UNAVAILABLE", ...)

resultados_completos = presentar(job.query, contador_total, top=None)   # ← SIN recortar
informe = {job_id, ts, archivo, tamano_bytes, query, top, chunk_mb,
           chunks, workers, lineas, invalidas, duracion, resultados}
await asyncio.to_thread(self._cola_informes.put, informe)
return "encolada"
```

**Detalle con intención:** al cliente se le manda el agregado **recortado
al `--top`**, pero a SQLite se guarda el **agregado completo**
(`top=None`). Así un informe histórico sirve para cualquier `top`
posterior sin reprocesar el archivo.

Y: **el coordinador encola, no escribe.** No abre SQLite nunca. Ese es
el invariante 7.

#### 4.9 Supervisión del proceso hijo

```python
async def _supervisar_indexador(self):
    while True:
        await asyncio.sleep(2.0)
        if self._proceso_indexador is not None and self._servicio_disponible \
           and not self._proceso_indexador.is_alive():
            self._servicio_disponible = False
            logger.critical("El indexador terminó inesperadamente (exitcode=%s) …")
```

`is_alive()` y `exitcode` **no bloquean**: solo consultan el estado ya
conocido del proceso, no hacen `wait()`. Por eso esta corrutina puede
vivir dentro del loop sin violar el invariante 1.

Qué pasa cuando se cae el indexador (CU-23): se dejan de admitir
análisis nuevos con `SERVICE_UNAVAILABLE`, y un job **ya en curso**
falla con el mismo código al intentar persistir. La decisión de diseño
es explícita: **es mejor fallar que anunciar un éxito que no se puede
honrar.**

#### 4.10 Señales — `SIGINT`, `SIGTERM`, `SIGUSR1`

**📌 Concepto: `loop.add_signal_handler` y por qué el handler no hace nada**

```python
loop.add_signal_handler(signal.SIGINT,  self._agendar_apagado)
loop.add_signal_handler(signal.SIGTERM, self._agendar_apagado)
loop.add_signal_handler(signal.SIGUSR1, self._agendar_snapshot)

def _agendar_apagado(self):
    asyncio.ensure_future(self._apagar())    # solo AGENDA la corrutina
```

Un handler de señal corre en un contexto restringido: puede interrumpir
el intérprete en cualquier punto, así que **no debe hacer I/O
bloqueante ni esperar locks**. La disciplina acá es: el handler solo
**agenda** una corrutina con `ensure_future` y vuelve de inmediato; todo
el trabajo real ocurre después, en el loop, con las reglas normales de
`asyncio`. `loop.add_signal_handler` además garantiza que la corrutina
se agende en el hilo del loop (al contrario de `signal.signal`, que
ejecutaría el callback en cualquier punto).

**`SIGUSR1` → `_emitir_snapshot`** (observabilidad sin API de admin):
loguea cuántos jobs hay activos, y por cada uno su `job_id`, estado,
progreso `n/total` y tiempo transcurrido, más el snapshot de los cuatro
contadores IPC. **No interrumpe a ningún cliente**: hay un test que
manda la señal de verdad con `os.kill` mientras un job corre y verifica
que el job sigue.

Fijate que la lectura del diccionario se hace bajo `async with
self._lock` y **se copia a una lista de tuplas** antes de loguear: el
logueo (que es I/O) queda **fuera** de la sección crítica. Eso es el
invariante 5 — nada de ceder control a mitad de una sección crítica.

**`SIGINT`/`SIGTERM` → `_apagar`** (cierre ordenado, CU-19), en orden:

1. `if self._cerrando: return` → **idempotente**: dos Ctrl-C no
   duplican el apagado (hay test).
2. `self._server.close()` → deja de aceptar conexiones nuevas.
3. A cada conexión activa se le manda `SERVER_SHUTDOWN` y se le
   **cancela** su tarea; después `asyncio.gather(..., return_exceptions=True)`
   espera a que todas terminen.
4. `cola_informes.put(None)` → el **centinela** de cierre, puesto
   *después* de todo lo ya encolado, así el hijo drena lo pendiente
   antes de salir.
5. `await asyncio.to_thread(proceso.join, 30.0)` → se espera al hijo
   **fuera del loop** (un `join` es bloqueante).
6. Si a los 30 s sigue vivo: se loguea un error explícito diciendo que
   **no se garantiza drenaje completo** y se hace `terminate()` + join
   de 5 s. La terminación forzada es un último recurso **registrado**,
   no un cierre que prometa nada.
7. Se cierra la cola, se cancela el supervisor y se marca
   `_evento_cierre`, que es lo que desbloquea a `_servir` y termina el
   proceso.

#### 4.11 La CLI del coordinador

| Flag | Default | Qué controla |
|---|---|---|
| `-i`/`--ip` | `0.0.0.0` | Interfaz de escucha |
| `-p`/`--port` | `8888` | Puerto (validado 1–65535) |
| `--chunk-mb` | `32` | Tamaño de tramo por default (1–256 MiB) |
| `--max-jobs` | `3` | Cupo del `Semaphore` de admisión |
| `--job-timeout` | `300` | Segundos desde la admisión hasta el fin del reduce |
| `--broker` | — | URL de Redis (si se omite, `CELERY_BROKER_URL`) |
| `--s3-endpoint` | — | Endpoint de MinIO (si se omite, `S3_ENDPOINT`) |
| `--db` | `informes.sqlite` | Ruta de SQLite del indexador |

#### 4.12 Tests del coordinador (3 archivos, 19 tests)

La técnica común: **inyección de dependencias**. El constructor acepta
`lanzar_grupo`, `reducir_resultados`, `cola_informes` y `metricas`, así
que los tests pasan dobles controlables por `threading.Event` y pueden
decidir con precisión *cuándo* "termina" cada tramo, sin Redis ni
Celery reales.

`tests/test_coordinador.py` (9 tests) — la capa de jobs:

- dos clientes concurrentes **no mezclan** progreso ni resultados (invariante 8);
- con `--max-jobs 1`, el segundo cliente recibe `queued` y arranca al liberarse el cupo;
- el **timeout de un job no cancela al otro** y libera su cupo;
- un fallo de tarea produce `TASK_FAILED` **y revoca el grupo**;
- fallo al publicar → `BROKER_UNAVAILABLE`;
- desconexión **mientras espera cupo** → lo libera **sin publicar tareas**;
- desconexión **durante la ejecución** → revoca el grupo y libera el cupo;
- un pedido inválido **no crea job ni consume cupo**;
- archivo vacío → 0 tareas e informe vacío.

`tests/test_coordinador_persistencia.py` (6 tests) — el camino de persistencia:
que se encola el **agregado completo antes del `top`**; que sin cola la
persistencia queda `"no_configurada"`; que con el servicio caído se
rechaza **sin consumir cupo**; que un indexador que cae a mitad de job
da `SERVICE_UNAVAILABLE` **sin encolar**; que un fallo al encolar también;
y que las métricas cuentan emisión y líneas válidas.

`tests/test_operacion.py` (5 tests) — señales **de verdad** (`os.kill`
al propio proceso, porque es la única forma de probar que el handler
quedó bien instalado y no bloquea): `SIGTERM` notifica shutdown, drena y
resuelve el cierre; el apagado es idempotente ante doble señal; un
pedido posterior al cierre recibe `SERVER_SHUTDOWN`; `SIGUSR1` emite el
snapshot **sin interrumpir al cliente**; el supervisor detecta la caída
**real** del indexador y bloquea la admisión.

---

### 5. `src/loghunter/lector.py` — lectura por rangos (la pieza que hace posible distribuir)

**Qué es:** la abstracción que permite que un worker lea **solo su
tramo** del archivo, sin importar en qué máquina esté. Es la pieza que
convierte "procesos paralelos locales" en "sistema distribuido".

**El contrato, igual para los dos backends:**

| Método | Qué hace |
|---|---|
| `tamano()` | Tamaño total en bytes (se obtiene **una sola vez** al abrir) |
| `leer(inicio, fin)` | Exactamente los bytes `[inicio, fin)` — rango **semiabierto** |
| `leer_hasta_salto(desde, max_bytes)` | Desde `desde` hasta el primer LF **inclusive**, o hasta `max_bytes`/EOF |
| `cerrar()` | Libera recursos. **Idempotente** |

`abrir(uri)` es la *factory*: mira el esquema de la URI y devuelve
`LectorLocal` (para `local://` o ruta sin esquema) o `LectorS3` (para
`s3://`). **El esquema de la URI, no un flag, decide el backend.** Los
dos soportan `with` (`__enter__`/`__exit__`).

#### `LectorLocal` — `mmap`

```python
self._fd = os.open(self._ruta, os.O_RDONLY)
self._tamano = os.fstat(self._fd).st_size
```

**📌 Concepto: ¿por qué `mmap` y no `seek` + `read`?**

`mmap` mapea el archivo en el espacio de direcciones virtuales del
proceso. Las lecturas van directo contra el **page cache del kernel**:
no hay una copia de kernel-space a user-space por cada `read`, y las
**relecturas solapadas entre tramos vecinos son prácticamente gratis**
porque las páginas ya están cacheadas. Eso importa mucho acá: la
alineación a fin de línea hace que los tramos vecinos relean unos pocos
bytes del otro.

El detalle técnico de `leer()` es la **granularidad de alineación**:

```python
granularidad = mmap.ALLOCATIONGRANULARITY
offset_alineado = (inicio // granularidad) * granularidad
desplazamiento  = inicio - offset_alineado
longitud        = fin - offset_alineado
with mmap.mmap(self._fd, length=longitud, access=mmap.ACCESS_READ,
               offset=offset_alineado) as mapa:
    return bytes(mapa[desplazamiento : desplazamiento + (fin - inicio)])
```

`mmap` **exige** que el `offset` sea múltiplo de la granularidad de
asignación del sistema (4096 en Linux/macOS). Como `inicio` es un offset
arbitrario, se redondea hacia abajo al múltiplo anterior, se mapea un
poco más de lo necesario, y se recorta con el `desplazamiento` interno.
Si no se hiciera, `mmap` lanzaría `ValueError` en cualquier tramo que no
empiece en múltiplo de 4096 — o sea, casi todos.

`_validar_rango` exige `0 <= inicio <= fin <= tamano` y lanza
`ValueError` si no: un rango fuera del archivo es un bug de
planificación, no un error de datos.

#### `LectorS3` — `HEAD` + `GET Range`

```python
self._tamano = head_object(...)["ContentLength"]              # metadatos
get_object(..., Range=f"bytes={inicio}-{fin - 1}")            # ← OJO: -1
```

**📌 Concepto: HTTP `Range` es inclusivo, el contrato interno es semiabierto.**
`[inicio, fin)` en Python ↔ `bytes=inicio-(fin-1)` en HTTP. Ese `-1` es
el tipo de detalle que un tribunal pregunta.

`cliente_s3()` configura `boto3` con: `addressing_style: "path"`
(MinIO no usa subdominios por bucket como AWS), `connect_timeout=5`,
`read_timeout=15` y **`retries={"max_attempts": 0}`** — los reintentos
de `botocore` se **desactivan a propósito** para manejarlos nosotros con
política explícita.

**📌 Concepto: reintentos finitos con backoff, y distinguir transitorio de permanente**

`_es_transitorio()` clasifica el error:

- **Permanente** (no se reintenta, fallar ya): `NoSuchKey`,
  `NoSuchBucket`, `AccessDenied`, `403`, `404`. Reintentar un 404 es
  perder tiempo: el objeto no va a aparecer.
- **Transitorio** (vale reintentar): HTTP ≥ 500, `SlowDown`,
  `RequestTimeout`, `InternalError`, `ServiceUnavailable`, y
  `EndpointConnectionError`.

`_operacion_con_reintentos()` reintenta hasta 3 veces con **backoff
exponencial** (`0.2 · 2^intento` → 0.2 s, 0.4 s, 0.8 s). El backoff
existe para no agravar la congestión del servicio que ya está
sufriendo. Si se agotan, lanza `AlmacenamientoNoDisponible`, que el
coordinador traduce a `STORAGE_UNAVAILABLE`. **Los reintentos son
finitos**: nunca se espera indefinidamente.

`_traducir_error()` mapea cada error de S3 a la excepción de Python
equivalente (`FileNotFoundError`, `PermissionError`,
`AlmacenamientoNoDisponible`) **para que el resto del sistema no sepa
nada de S3**: el coordinador maneja las mismas excepciones vengan de un
archivo local o de MinIO.

**La optimización del issue #53 — el caché de lectura adelantada.**
Esto es un bug real que se encontró y arregló, y es una anécdota
excelente para la defensa:

`_lineas_del_tramo` (en `workers.py`) llama a `leer_hasta_salto` **una
vez por línea**. En `LectorLocal` eso es casi gratis: `mmap` + page
cache. En `LectorS3`, **cada llamada sería un `GET Range` HTTP real**.
Un tramo de 32 MB con líneas de 150 bytes son ~220.000 peticiones HTTP,
cada una trayendo hasta 64 KiB para usar 150 bytes. La solución:

```python
_S3_LECTURA_ADELANTADA = 512 * 1024   # 512 KiB

cache_cubre = (self._cache_inicio <= desde
               and desde + ventana <= self._cache_inicio + len(self._cache_datos))
if not cache_cubre:
    objetivo = min(desde + max(max_bytes, _S3_LECTURA_ADELANTADA), self._tamano)
    self._cache_datos = self.leer(desde, objetivo)
    self._cache_inicio = desde
```

Se trae un bloque de 512 KiB y se sirven de ahí todas las líneas que
caigan dentro, mientras el cursor siga en el bloque. Pasa de ~3400
peticiones por MB a ~2. Hay un test dedicado
(`test_leer_hasta_salto_muchas_lineas_cortas_no_dispara_un_get_por_linea`)
que cuenta las llamadas a `get_object` y falla si vuelve a degradarse.

**Funciones auxiliares:** `normalizar_uri_local()` (ruta o `local://` →
`local:///ruta/absoluta` resuelta), `normalizar_uri_s3()`
(`s3://bucket/key` → `(bucket, key)`), `_leer_hasta_salto_generico()`
(la lógica compartida de "hasta el primer LF").

**Tests:** `tests/test_lector.py` (11 tests, local): tamaño real, rango
no alineado a granularidad, rango **sí** alineado, `leer_hasta_salto`
con y sin LF, rango fuera del archivo rechazado, archivo inexistente,
archivo vacío (**no se mapea**: `mmap` de longitud 0 falla), `cerrar()`
idempotente, backend desconocido rechazado.
`tests/test_lector_s3.py` (17 tests, con un cliente S3 doble —
documentan que se descartó `moto` porque su versión no intercepta bien
con el `botocore` de este entorno): bucket/key, endpoint y
`addressing_style`, rango vacío que **no** llama a `get_object`,
objeto inexistente, acceso denegado, **los mismos rangos producen los
mismos bytes que local**, la clasificación transitorio/permanente, los
reintentos agotados y la recuperación tras un fallo transitorio.

---

### 6. `src/loghunter/workers.py` — el núcleo del map-reduce

**Qué es:** dos funciones puras, sin Celery, sin red, sin estado. Son el
corazón algorítmico del proyecto y están **deliberadamente separadas**
de la tarea Celery: así se pueden probar en memoria, y la tarea
distribuida las reutiliza sin cambios.

#### `calcular_rangos(tamano, chunk_bytes)` — la planificación

```python
cantidad = math.ceil(tamano / chunk_bytes)
return [(i*chunk_bytes, min(i*chunk_bytes + chunk_bytes, tamano))
        for i in range(cantidad)]
```

**Aritmética pura. No abre el archivo, no lee un byte.** Es lo que
permite que el coordinador planifique sin acceder al contenido
(invariante 3). Un archivo de 2 GiB con tramos de 32 MiB da 64 tareas; un
archivo vacío da **cero** tramos.

#### `map_chunk(uri, inicio, fin, query)` — el map

```python
lector = lector_mod.abrir(uri)          # ← el worker resuelve su propio acceso
try:
    for linea in _lineas_del_tramo(lector, inicio, fin):
        registro = parsear_linea(linea)
        if registro is None: invalidas += 1
        else:                validas += 1; registros.append(registro)
finally:
    lector.cerrar()
contador = agregar_parcial(query, registros)
return {"contador": dict(contador), "validas": validas, "invalidas": invalidas}
```

**📌 Concepto: idempotencia (invariante 6).** El resultado depende
**solo** de `(uri, inicio, fin, query)`. No muta nada externo, no
escribe archivos, no depende de ningún estado dejado por una ejecución
anterior. Por qué importa: Celery con `task_acks_late` **reentrega**
tareas cuyo worker murió a mitad de camino. Si `map_chunk` no fuera
idempotente, un reintento podría duplicar o perder líneas. Siendo
idempotente, el reintento recalcula el mismo tramo desde cero y produce
exactamente el mismo `Counter` — **por eso un worker caído no corrompe
el resultado**.

#### Las tres funciones de alineación — acá está el algoritmo clave

```python
def _lineas_del_tramo(lector, inicio, fin):
    cursor = _inicio_alineado(lector, inicio)
    while cursor < fin:                      # ← la condición es sobre el INICIO de la línea
        linea = _leer_una_linea(lector, cursor)
        if not linea: return
        cursor += len(linea)
        yield linea
```

```python
def _inicio_alineado(lector, inicio):
    if inicio == 0:
        return 0                             # el primer tramo arranca en el primer byte
    anterior = lector.leer(inicio - 1, inicio)
    if anterior == b"\n":
        return inicio                        # ya estamos justo al inicio de una línea
    # si no: descartar la cola de la línea del tramo anterior
    cursor = inicio
    while True:
        bloque = lector.leer_hasta_salto(cursor, _BLOQUE)   # _BLOQUE = 64 KiB
        if not bloque:
            return lector.tamano()
        cursor += len(bloque)
        if bloque.endswith(b"\n"):
            return cursor
```

```python
def _leer_una_linea(lector, desde):
    # acumula bloques de 64 KiB hasta encontrar LF o EOF
    # → una línea MÁS LARGA que 64 KiB se lee completa, en varias pasadas
```

El `_BLOQUE = 64 * 1024` tiene un comentario explícito en el código:
*"bytes de lectura auxiliar; **no es una cota de longitud de línea**"*.
Es tamaño de bloque de I/O, no un límite. Hay un test con una línea de
más de 64 KiB que verifica que se lee entera.

**El detalle que hace que la condición del `while` sea correcta:**
`while cursor < fin` compara el **offset del primer byte de la línea**
contra `fin`. Entonces la última línea de un tramo se lee **completa**,
aunque su contenido se extienda más allá de `fin`. Y el tramo siguiente
la descarta, porque el byte anterior a su `inicio` no era un LF. Nunca
se cuenta dos veces, nunca se pierde.

**Por qué la alineación la hace el worker y no el coordinador:**
si el coordinador alineara los cortes, tendría que **leer el contenido**
del archivo, y eso rompe el invariante 3 y acopla planificación con
lectura. Haciéndolo en el worker, el coordinador se mantiene puramente
aritmético.

**Tests (`tests/test_workers.py`, 16 tests)** — es el archivo de tests
más interesante del proyecto, porque enumera todos los casos borde del
corte:

- `calcular_rangos`: 128 MiB / 32 MiB = 4 rangos; 2 GiB / 32 MiB = 64 rangos; archivo vacío = 0 rangos; `chunk_bytes` no positivo rechazado.
- corte **exactamente después de un LF** → no pierde ni duplica;
- corte **dentro de una línea** → no pierde ni duplica;
- corte **justo después del último byte** del archivo;
- corte **entre el CR y el LF** de un CRLF;
- corte **sobre un carácter UTF-8 multibyte** → no corrompe la línea (porque se particiona **antes** de decodificar);
- **última línea sin salto final** se cuenta;
- **línea mayor a 64 KiB** se lee completa;
- **línea que atraviesa varios tramos** se cuenta **una sola vez**;
- **tramo sin ningún inicio de línea** no produce registros (puede pasar con una línea muy larga y tramos chicos);
- `map_chunk` es idempotente; sobre tramo vacío no produce registros;
- y el test integrador: **map-reduce coincide con la referencia secuencial** para varios tamaños de tramo.

---

### 7. `src/loghunter/parser.py` — interpretar una línea combined

**Qué es:** convierte `bytes` de una línea en un `Registro` validado, o
`None` si la línea es inválida. No sabe cómo se leyó la línea ni cómo se
va a transportar el resultado.

**El formato combined de Apache/Nginx:**

```
192.168.1.45 - - [20/Oct/2026:14:32:11 -0300] "GET /api/users HTTP/1.1" 200 1534 "-" "curl/8.1"
└── IP ───┘ │ │ └────── timestamp ──────────┘ └──── petición ────────┘ │   │    │     │
          ident user                                                 estado bytes ref user-agent
```

**La estructura de salida:**

```python
@dataclass(frozen=True, slots=True)
class Registro:
    ip: str; timestamp: datetime; metodo: str; destino: str; estado: int; bytes: int
```

`frozen=True` → inmutable (no se puede corromper después de validado).
`slots=True` → sin `__dict__`, mucho menos memoria por instancia. Con
millones de registros eso se nota.

**`parsear_linea(linea: bytes) -> Registro | None`**, en orden:

1. `rstrip(b"\r\n")` → tolera LF, CRLF **o ausencia de salto** (la última línea del archivo). Línea vacía → inválida.
2. `decode("utf-8", errors="strict")` → una línea con bytes no-UTF8 es inválida, no explota.
3. `_PATRON_REGISTRO.match(texto)` → la regex del formato completo.
4. `ipaddress.ip_address(...)` → **valida y normaliza** la IP. Acepta IPv4 **e IPv6**. `010.0.0.1` o `999.1.1.1` se rechazan.
5. Mes por nombre (`_MESES`: `Jan`→1…), offset horario con signo (`-0300` → `timedelta(hours=-3)`), y `datetime(...)` dentro de un `try` — una fecha imposible como `31/Feb` da `ValueError` → inválida.
6. `_PATRON_PETICION` sobre la petición **desescapada** → extrae método y destino; exige `HTTP/x.y`.
7. `100 <= estado <= 599` → un `999` es inválido.
8. `bytes`: un `-` significa **0** (cero bytes enviados), no es un error.

**Decisión de diseño importante:** una línea inválida **no aborta
nada**. Devuelve `None` y quien llama decide cómo contarla. Esa es la
razón de que el sistema pueda procesar un log real con basura adentro
sin caerse — se cuenta en `invalidas` y se sigue.

`_desescapar()` revierte los `\"` y `\\` de un campo citado, porque un
user-agent puede contener comillas escapadas.

**Tests (`tests/test_parser.py`, 16 tests):** el ejemplo normativo del
contrato (5 válidos + 1 inválido); extracción de todos los campos;
**IPv6**; `bytes` igual a `-` ⇒ 0; escape de comillas; CRLF y ausencia
de salto final; offset horario positivo; y los rechazos: línea vacía, IP
inválida, fecha inválida, mes desconocido, estado fuera de rango, estado
no numérico, bytes negativos, UTF-8 inválido, campo citado no cerrado.

---

### 8. `src/loghunter/consultas.py` — agregadores y reduce

**Qué es:** las cuatro consultas, el reduce, la presentación, y la
implementación de **referencia secuencial** usada como oráculo en los
tests.

#### `_clave_valor(query, registro)` — qué cuenta cada consulta

| Consulta | Clave | Valor que acumula |
|---|---|---|
| `top-ips` | `registro.ip` | `1` (cuenta ocurrencias) |
| `status` | `str(registro.estado)` | `1` |
| `urls` | `registro.destino` (**con query string**: `/a?x=1` ≠ `/a`) | `1` |
| `traffic` | hora UTC truncada (`"2026-01-02T04:00:00Z"`) | `registro.bytes` (**suma tráfico**) |

Para `traffic` hay un paso sutil: `astimezone(timezone.utc)` **antes**
de truncar a la hora. Si no se normalizara a UTC, dos registros de la
misma hora real pero con offsets distintos caerían en claves distintas.

#### `agregar_parcial(query, registros) -> Counter[str]`

Acumula un tramo en un `Counter`. **Por qué `Counter` y no un dict
cualquiera:** `Counter.update()` **suma** valores por clave en vez de
sobrescribirlos, así que los parciales son **combinables sin perder
exactitud**. Eso es lo que hace que el reduce sea una simple suma.

#### `reducir(parciales) -> Counter[str]`

```python
total = Counter()
for parcial in parciales:
    total.update(parcial)
return total
```

El reduce completo del map-reduce, en cuatro líneas. Es **asociativo y
conmutativo**: el resultado no depende del orden en que lleguen los
parciales. Por eso no importa en qué orden terminen los workers.

#### `presentar(query, total, top=None)` — el orden y el recorte

```python
if query in ("top-ips", "urls"):
    items = sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))   # cantidad ↓, clave ↑
    return items[:top] if top is not None else items
if query == "status":
    return sorted(total.items(), key=lambda kv: int(kv[0]))         # numérico ascendente
return sorted(total.items(), key=lambda kv: kv[0])                  # traffic: cronológico
```

**📌 Concepto central del map-reduce: el `top` se aplica DESPUÉS de reducir, nunca dentro de un tramo.**

Esto es correctitud, no estilo. Si cada worker devolviera solo su top-10
local, el top-10 global podría ser **incorrecto**: una IP que es la #11
en cada uno de 64 tramos puede ser la #1 del archivo completo, y se
habría descartado. Hay un test dedicado a demostrarlo
(`test_top_global_difiere_de_tops_locales_se_resuelve_con_reduce`).
Por eso cada worker devuelve su `Counter` **completo**.

Y `--top` **solo recorta `top-ips` y `urls`**. `status` devuelve siempre
todos los códigos y `traffic` todas las horas: recortar una serie
temporal no tendría sentido. El desempate es por **clave ascendente**
(`-kv[1], kv[0]`), no por orden de llegada — así el resultado es
**determinista** aunque los workers terminen en cualquier orden, que es
condición para poder comparar corridas.

#### `calcular_referencia(ruta, query)` — el oráculo

Procesa el archivo completo **en un solo proceso, sin fragmentar, sin
usar `lector.py`**, iterando el archivo línea por línea con el iterador
nativo de Python. Devuelve `(contador, validas, invalidas)`.

**No es código de producción.** Es la implementación "obviamente
correcta" contra la que se compara el resultado distribuido. Es la
estrategia de verificación central del proyecto: *si partir el archivo
en 64 tramos y procesarlos en 8 procesos da exactamente el mismo
`Counter` que leerlo secuencialmente de arriba a abajo, entonces el
particionado es correcto*.

**Tests (`tests/test_consultas.py`, 11 tests):** `top-ips` coincide con
el ejemplo normativo; `status` en orden numérico; `urls` **no fusiona
query strings distintos**; `traffic` agrupa por hora UTC; `--top`
recorta solo `top-ips`/`urls`; días distintos no se mezclan en
`traffic`; el desempate es por clave ascendente; **el top global difiere
de los tops locales y se resuelve con el reduce**; consulta desconocida
rechazada; la referencia secuencial coincide con el agregado en memoria;
y sobre archivo vacío.

---

### 9. `src/loghunter/tasks.py` — la app Celery y la tarea distribuida

**Qué es:** el archivo que convierte la función pura `workers.map_chunk`
en una **tarea distribuida** que puede ejecutarse en otro proceso, en
otra máquina. Es **acá donde vive el paralelismo real.**

**📌 Concepto: broker vs backend de resultados**

```python
broker_url     = "redis://localhost:6379/0"   # la COLA: tareas pendientes
result_backend = "redis://localhost:6379/1"   # los RESULTADOS: parciales devueltos
```

Son **dos funciones distintas** que acá cumple el mismo Redis en **dos
bases distintas** (`/0` y `/1`). El broker es el canal por el que viajan
las tareas; el backend es donde el worker deja su `Counter` para que el
coordinador lo recoja. RabbitMQ habría sido un broker válido, pero
habría exigido un segundo almacén para resultados — Redis cubre ambas con
un servicio (ADR-0003).

**📌 Concepto: el pool `prefork` — acá está el paralelismo**

`celery -A loghunter.tasks worker -c 4` lanza un worker con **4 procesos
hijos** (`fork`). Cada uno ejecuta tareas de forma independiente, en su
propio intérprete, con su propio GIL. **Cuatro núcleos trabajando de
verdad al mismo tiempo.** Con `--scale worker=2` y `-c 4` son **8
procesos** totales: son dos ejes independientes de escalado, y conviene
no confundirlos en la defensa (está medido en
[OPERACION.md](OPERACION.md#escalar-el-paralelismo-de-workers)).

#### `configuracion_desde_entorno()` y `crear_app()`

Toda la configuración sale de variables de entorno, con defaults
explícitos. Las opciones que importan y **por qué**:

| Opción | Valor | Por qué |
|---|---|---|
| `task_serializer` / `result_serializer` / `accept_content` | `json` | **Nunca `pickle`**, misma razón de seguridad que el protocolo TCP |
| **`task_acks_late = True`** | | **La clave de la recuperación.** La tarea se confirma al broker **después** de terminar, no al recibirla. Si el worker muere a mitad, la tarea **nunca fue confirmada** y el broker la vuelve a entregar a otro worker |
| **`task_reject_on_worker_lost = True`** | | Si se pierde el ejecutor, la tarea se **rechaza** (vuelve a la cola) en vez de marcarse fallida |
| `task_time_limit` | `120` s | Una tarea colgada no retiene un proceso para siempre |
| `visibility_timeout` | `180` s | Cuánto espera Redis antes de considerar que una tarea entregada se perdió y reentregarla |
| `result_expires` | `3600` s | Los resultados no se acumulan en Redis para siempre |

**📌 Concepto: recuperación ante caída de un worker — y la honestidad del diseño**

```
worker procesa tramo [32MB, 64MB)  →  muere a mitad (kill -9 / se cae la máquina)
   ↓ la tarea nunca se confirmó (acks_late)
Redis la reentrega a otro worker cuando vence el visibility_timeout
   ↓ map_chunk es IDEMPOTENTE
el nuevo worker recalcula [32MB, 64MB) desde cero → mismo Counter
   ↓
el job termina con el resultado correcto, sin duplicar ni perder líneas
```

La frase precisa para la defensa (y está así en ADR-0003): **se asume
posible re-ejecución de tareas, no ejecución exactamente una vez.** No
se promete *exactly-once* — se promete que la re-ejecución es inofensiva
porque la tarea es idempotente. Ese es el tipo de afirmación calibrada
que conviene decir, en vez de prometer garantías que el sistema no da.

Y **la tarea no reintenta por sí misma**: un error permanente (archivo
inexistente) debe **propagar** y marcar la tarea `FAILURE`, para que el
coordinador responda `TASK_FAILED` en vez de reintentar algo que nunca
va a funcionar. La recuperación ante pérdida de ejecutor la da la
**confirmación tardía del broker**, no código de aplicación.

#### La tarea `map_chunk`

```python
@app.task(name="loghunter.map_chunk", bind=True)
def map_chunk(self, uri, inicio, fin, query, job_id):
    logger.info("map_chunk job_id=%s task_id=%s uri=%s rango=[%d,%d) query=%s", ...)
    parcial = workers.map_chunk(uri, inicio, fin, query)   # ← reutiliza la función pura
    return {"contador": parcial["contador"],
            "lineas":   parcial["validas"],
            "invalidas":parcial["invalidas"],
            "worker":   {"hostname": socket.gethostname(), "pid": os.getpid()}}
```

Es una capa **finísima** a propósito: solo loguea, delega en la función
pura y adapta el resultado al contrato de red. El `{hostname, pid}` es
lo que permite **demostrar la distribución**: el informe final reporta
`workers observados`, contando hostnames/PIDs distintos. Es la evidencia
de que el trabajo se repartió de verdad entre procesos (y, con un worker
remoto, entre máquinas). `job_id` solo sirve para **correlacionar logs**;
no afecta el cómputo (hay un test que lo verifica).

El `if __name__ == "__main__"` lanza un `SystemExit` con el mensaje
correcto: este módulo no se ejecuta, se le pasa a
`celery -A loghunter.tasks worker`.

**Tests (`tests/test_tasks.py`, 6 tests):** se invoca la tarea con
`.run()` (sin broker, alcanza para el contrato): que adapta el resultado
de `workers` **sin cambiar el cómputo**; que es idempotente; que el
`job_id` no afecta el cómputo; que la configuración usa los defaults del
contrato y respeta las variables explícitas; y que **`crear_app`
configura la confirmación tardía y el rechazo al perder el worker**.

---

### 10. `src/loghunter/analisis.py` — lanzar el grupo y cosechar los parciales

**Qué es:** la capa que el coordinador usa (vía `asyncio.to_thread`)
para publicar las N tareas de un job y recoger sus resultados. Es código
**sincrónico y bloqueante** a propósito: su trabajo es correr en un hilo
auxiliar.

#### `Grupo` — por qué NO se usa `celery.result.GroupResult`

```python
@dataclass(frozen=True, slots=True)
class Grupo:
    id: str                                  # el job_id
    results: list[AsyncResult] = field(default_factory=list)
```

Decisión documentada en el código: `GroupResult` exige que cada
resultado implemente el protocolo de promesas (`.then()`) para su
barrera interna de "todos listos" — una barrera que **no queremos**.
Necesitamos sondear tarea por tarea para poder **abortar ante el primer
fallo definitivo sin esperar al 100%**. Así que `Grupo` es una
estructura mínima propia: el `job_id` más la lista de `AsyncResult`.

Eso **es** el invariante 8: cada job tiene su `job_id` y su propia lista
de resultados, y los parciales de pedidos simultáneos nunca se mezclan.

#### `lanzar_grupo(uri, tamano, chunk_bytes, query, job_id) -> Grupo`

```python
rangos = workers.calcular_rangos(tamano, chunk_bytes)
if not rangos:
    return Grupo(id=job_id, results=[])        # archivo vacío: 0 tareas

task_ids = [nuevo_job_id() for _ in rangos]    # ← PRE-generar los IDs
publicados = []
try:
    for task_id, (inicio, fin) in zip(task_ids, rangos):
        publicados.append(map_chunk.apply_async(
            args=(uri, inicio, fin, query, job_id), task_id=task_id))
except Exception:
    for resultado in publicados:
        resultado.revoke()                      # ← limpiar lo ya publicado
    raise
```

**📌 Concepto: la publicación parcial y por qué se pre-generan los `task_id`.**

Si Redis se cae **a mitad** de publicar los 64 tramos, 30 ya están en la
cola. **No alcanza con asumir que ninguno llegó**: hay que revocar los
ya publicados, o quedarían 30 workers procesando un job que nunca se va
a cosechar. Pre-generar los IDs **antes** de publicar nada es lo que
hace posible referirse a esas tareas para revocarlas. Hay un test
dedicado (`test_publicacion_parcial_fallida_revoca_lo_ya_publicado`).

Y acá se ve el invariante 2 cristalino: `args=(uri, inicio, fin, query,
job_id)` — por la cola viajan **coordenadas**, no bytes. Una tarea pesa
unos cien bytes sin importar si el tramo son 32 MB o 256 MB.

#### `reducir_resultados(grupo, timeout, intervalo) -> dict`

```python
deadline = None if timeout is None else time.monotonic() + timeout
pendientes = list(grupo.results)
parciales = []

while pendientes:
    for resultado in list(pendientes):          # ← copia: se mutará la lista
        if not resultado.ready():
            continue
        if resultado.failed():
            raise TareaFallida(f"Tarea {resultado.id} terminó con fallo definitivo: …")
        parciales.append(resultado.result)
        pendientes.remove(resultado)
    if pendientes:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"Venció el timeout esperando {len(pendientes)} de {len(…)} tareas")
        time.sleep(intervalo)
```

Tres decisiones que vale explicar:

1. **`ready()` no alcanza.** Un grupo "listo" puede contener fallos.
   Hay que distinguir `failed()` de éxito **por tarea**. Por eso no se
   usa la barrera de `GroupResult`.
2. **Se aborta ante el primer fallo definitivo**, sin esperar que
   terminen las otras 63. El coordinador después revoca el resto y
   responde `TASK_FAILED` rápido, en vez de hacer esperar al cliente un
   resultado que ya se sabe que no va a existir.
3. **Cada `AsyncResult` aporta su parcial exactamente una vez**: se lo
   saca de `pendientes` al cosecharlo. Hay test
   (`test_cada_task_id_aporta_una_sola_vez`).

El resultado agregado:

```python
return {"contador":  dict(reducir(p["contador"] for p in parciales)),
        "lineas":    sum(p["lineas"] for p in parciales),
        "invalidas": sum(p["invalidas"] for p in parciales),
        "workers":   len({(p["worker"]["hostname"], p["worker"]["pid"]) for p in parciales})}
```

Ese `len({(hostname, pid)})` es **la métrica que demuestra la
distribución**: cuántos procesos distintos participaron realmente del
job. Es el número que mostrás en la demo.

**Tests (`tests/test_analisis.py`, 8 tests)** con un doble de
`AsyncResult` (no requiere Redis): el reduce suma contadores, líneas e
inválidas; un grupo sin resultados devuelve agregado vacío sin iterar;
un **fallo definitivo levanta `TareaFallida` sin esperar al resto**; un
timeout vencido levanta `TimeoutError`; cada `task_id` aporta una sola
vez; un archivo vacío no publica ninguna tarea; **los `task_id` se
pre-generan distintos antes de publicar**; y una **publicación parcial
fallida revoca lo ya publicado**.

---

### 11. `src/loghunter/ipc.py` — contadores compartidos entre procesos

**Qué es:** el archivo más corto (77 líneas) y uno de los más
conceptuales. Cuatro contadores que el coordinador y el indexador —
**procesos distintos** — comparten.

**📌 Concepto: ¿por qué no una variable normal?**

Porque **los procesos no comparten memoria**. Si el coordinador hace
`self.contador += 1`, el indexador no ve nada: cada proceso tiene su
propio espacio de direcciones. Hace falta memoria compartida explícita,
y eso es `multiprocessing.Value`.

```python
def _contador():
    return multiprocessing.Value("q", 0)    # "q" = long long con signo, 64 bits
```

**📌 Concepto: `Value` con `lock=True` NO protege `+=` (invariante 4)**

Esto es el punto que más se pregunta. `multiprocessing.Value` crea un
`Lock` propio y lo expone con `get_lock()`, pero **no lo toma
automáticamente** en `.value += 1`. Y `+= 1` **no es atómico**: son
**tres** operaciones separadas:

```
1. leer   valor  (= 5)
2. sumar  1      (= 6)
3. escribir      (→ 6)
```

Si dos procesos hacen eso a la vez, los dos pueden leer `5`, los dos
calculan `6`, y los dos escriben `6`. **Un incremento se perdió.** Eso
es una *lost update*, el ejemplo canónico de *race condition*.

La solución, en cada método:

```python
def incrementar_jobs_activos(self):
    with self.jobs_activos.get_lock():      # ← el lock se toma EXPLÍCITAMENTE
        self.jobs_activos.value += 1
```

Y **`.value` crudo nunca se expone**, precisamente para que ningún otro
módulo pueda acceder sin lock. Incluso la **lectura** va bajo lock
(`_leer()`), porque leer sin lock podría ver un valor a medio escribir.

#### Los cuatro contadores y quién es responsable de cada uno

| Contador | Responsable | Cuándo cambia |
|---|---|---|
| `jobs_activos` | **coordinador** | +1 al adquirir el cupo, −1 en el `finally` de `_procesar_job` (el único que puede **bajar**) |
| `informes_emitidos` | **coordinador** | +1 tras enviar una respuesta terminal exitosa |
| `informes_persistidos` | **indexador** | +1 **solo tras un commit real** de SQLite |
| `lineas_validas` | **coordinador** | se suma una vez por job, tras un reduce exitoso |

Asignar **un responsable único por métrica** es una decisión de diseño
(ADR-0009): evita que dos procesos "interpreten" el mismo evento de
forma distinta y lo cuenten dos veces.

`snapshot()` devuelve los cuatro de una vez, cada uno bajo su propio
lock. Es lo que imprime `SIGUSR1`.

**📌 Dos tipos de lock, para dos problemas distintos**

| | `multiprocessing.Lock` (`ipc.py`) | `asyncio.Lock` (`coordinador.py`) |
|---|---|---|
| Protege contra | **Preemption real** entre procesos/hilos del SO | **Carreras lógicas** que cruzan un `await` |
| Hay paralelismo real | Sí — dos procesos pueden escribir al mismo instante | No — una corrutina a la vez |
| Por qué hace falta | `+=` no es atómico | El patrón *leer → `await` → escribir* |

Si te preguntan "¿para qué un lock si `asyncio` es monohilo?": `asyncio`
elimina las *data races*, no las carreras lógicas. Y el lock de
`multiprocessing` resuelve un problema completamente distinto: ahí **sí**
hay concurrencia real del SO.

**Tests (`tests/test_ipc.py`, 2 tests).** El segundo es el importante y
está muy bien pensado: `test_incrementos_concurrentes_entre_procesos_no_se_pierden`
lanza **varios procesos reales** incrementando la misma métrica y
verifica que no se pierde ninguno. El docstring lo dice explícito: *"lo
que hay que demostrar no es que `+=` funcione en un solo proceso (eso no
prueba nada sobre IPC), sino que el lock evita incrementos perdidos
cuando varios procesos escriben la misma métrica a la vez"*.

---

### 12. `src/loghunter/indexador.py` — el proceso hijo que persiste

**Qué es:** un **proceso hijo** que el coordinador lanza con
`multiprocessing.Process`, y que es **el único escritor de SQLite** en
todo el sistema (invariante 7).

**📌 Concepto: ¿por qué un proceso aparte y no escribir desde el coordinador?**

Dos razones, y las dos son buenas:

1. **No bloquear el event loop** (invariante 1). `sqlite3` es
   sincrónico y un `INSERT` con `fsync` puede tardar. Hacerlo dentro del
   loop congelaría la atención de **todos** los clientes mientras se
   escribe en disco.
2. **Un único escritor hace viable SQLite.** SQLite no está pensado para
   escrituras concurrentes (bloqueo a nivel de archivo, `database is
   locked`). Serializando toda la escritura en un proceso, SQLite
   alcanza perfectamente y no hace falta un motor con servidor
   (ADR-0008). **Esa es la razón por la que la elección de SQLite es
   defendible.**

El indexador **no tiene imagen Docker propia** — no es un quinto
servicio del Compose. Es un hijo dentro del contenedor del coordinador.

#### El esquema SQLite

```sql
CREATE TABLE informes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,        -- ← la clave de la idempotencia
    ts REAL NOT NULL, archivo TEXT NOT NULL, tamano_bytes INTEGER NOT NULL,
    query TEXT NOT NULL, top INTEGER NOT NULL, chunk_mb INTEGER NOT NULL,
    chunks INTEGER NOT NULL, workers INTEGER,
    lineas INTEGER NOT NULL, invalidas INTEGER NOT NULL, duracion REAL NOT NULL
);
CREATE TABLE resultados (
    informe_id INTEGER NOT NULL REFERENCES informes(id),
    clave TEXT NOT NULL, valor INTEGER NOT NULL,
    PRIMARY KEY (informe_id, clave)
);
```

Dos tablas: los **metadatos** del job y el **agregado completo** (una
fila por clave). `conectar()` además hace `PRAGMA foreign_keys = ON`
(SQLite las tiene **apagadas** por default) y aplica el esquema con
`CREATE TABLE IF NOT EXISTS`, así arrancar sobre una base existente no
falla.

#### `guardar_informe(conn, informe) -> bool` — atomicidad

```python
with conn:                                    # ← transacción: commit o ROLLBACK total
    ya_existe = conn.execute("SELECT 1 FROM informes WHERE job_id = ?", (…,)).fetchone()
    if ya_existe is not None:
        return False                          # entrega repetida: no duplicar
    cursor = conn.execute("INSERT INTO informes (…) VALUES (…)")
    informe_id = cursor.lastrowid
    conn.executemany("INSERT INTO resultados (…) VALUES (?, ?, ?)", [...])
return True
```

**El `with conn` es el punto clave:** es el *context manager* de la
conexión, que hace `COMMIT` si todo salió bien y **`ROLLBACK` si hay
excepción**. Metadatos y agregado completo entran **juntos o no entran**.
Nunca queda un informe con metadatos pero sin resultados.

**El detalle más fino del archivo — por qué un `SELECT` previo y no
capturar `IntegrityError`:**

`job_id UNIQUE` ya impediría el duplicado. Pero capturar
`IntegrityError` confundiría **dos casos distintos**:

- "este `job_id` ya estaba" → caso **esperado** (una entrega repetida),
  hay que ignorarlo en silencio y devolver `False`;
- "clave repetida en `resultados`" → **error real** que debe revertir,
  loguearse y **no** contar como persistido.

Con el `SELECT` previo **dentro de la misma transacción**, el primer
caso se detecta explícitamente y cualquier otro `IntegrityError`
propaga, como corresponde. Hay un test para cada uno.

#### `ejecutar(cola, ruta_db, metricas)` — el *target* del proceso hijo

```python
signal.signal(signal.SIGINT,  _ignorar_sigint)      # ← el hijo IGNORA SIGINT
signal.signal(signal.SIGTERM, _manejar_sigterm)     # ← SIGTERM levanta _CierrePorSenal

conn = conectar(ruta_db)
try:
    while True:
        try:
            informe = cola.get()                    # ← BLOQUEANTE
        except _CierrePorSenal:
            logger.info("SIGTERM recibido: cierro la conexión SQLite de forma controlada")
            break
        if informe is None:                         # ← CENTINELA de cierre
            break
        try:
            nuevo = guardar_informe(conn, informe)
        except Exception:
            logger.exception("Fallo guardando el informe job_id=%s; transacción revertida", …)
            continue                                # ← un informe malo no mata al indexador
        if nuevo:
            metricas.incrementar_informes_persistidos()
        else:
            logger.info("job_id=%s ya estaba persistido: entrega repetida ignorada", …)
finally:
    conn.close()
```

**📌 Concepto 1: el patrón del centinela (`None`)**

El hijo no puede saber "cuándo no van a venir más informes" mirando la
cola: una cola vacía solo significa "nada *todavía*". La solución
canónica es el **centinela**: el padre encola un `None` **después** de
todo lo real, y el hijo sale al recibirlo. Como las colas preservan el
orden FIFO, eso garantiza que el hijo **drena todo lo pendiente antes de
salir**. Es exactamente lo que hace `_apagar()` en el coordinador.

**📌 Concepto 2: el manejo de señales del hijo**

- **`SIGINT` se ignora** (`_ignorar_sigint` no hace nada). Por qué:
  cuando hacés Ctrl-C en la terminal, la señal va a **todo el grupo de
  procesos**, padre e hijo a la vez. Si el hijo muriera por `SIGINT`,
  moriría *antes* de que el padre pueda encolarle el centinela y se
  perderían los informes pendientes. Ignorándolo, **el cierre del hijo
  lo organiza el padre**, de forma ordenada (CU-19).
- **`SIGTERM` levanta `_CierrePorSenal`** (una excepción custom) desde
  el handler. Eso **interrumpe el `cola.get()` bloqueante**: sin esto,
  un hijo esperando en `get()` quedaría ahí indefinidamente. Es la vía
  de escape para un `terminate()` directo, y permite cerrar la conexión
  SQLite de forma controlada en vez de morir a mitad de una lectura.

**📌 Concepto 3: resiliencia del bucle**

El `except Exception: ... continue` es deliberado: **un informe que
falla no mata al indexador**. Se loguea con traceback, la transacción ya
revirtió, y se sigue con el siguiente. Y `informes_persistidos`
**solo** se incrementa si hubo un commit real — el contador no miente.

**Tests (`tests/test_indexador.py`, 5 tests):** que `conectar` crea el
esquema y habilita las claves foráneas; que se persisten metadatos y
agregado completo; que una **entrega repetida no duplica filas**; que un
error de integridad **revierte toda la transacción**; y
`test_ejecutar_consume_la_cola_hasta_el_centinela_y_persiste_en_un_proceso_hijo`,
que lanza un **proceso hijo real** con `multiprocessing.Process` y
`Queue` para demostrar el IPC de punta a punta.

---

## Parte 3 — El particionado en profundidad (la pregunta más probable)

Esto merece su propia sección porque es el núcleo conceptual del
proyecto y se malentiende fácil. **Vos pensabas que a cada worker se le
daba "una línea" o "un pedazo de líneas". No: se le da un rango de
BYTES, y el worker se encarga de los bordes.** Veamos por qué.

### 3.1 Las tres opciones posibles

#### Opción A — particionar por **líneas**

"Que cada worker procese 100.000 líneas."

**Por qué no se puede.** Para saber dónde empieza la línea 100.001 hay
que **haber leído y contado los 100.000 saltos de línea anteriores**. Las
líneas no tienen longitud fija: no hay forma de calcular el offset de la
línea N sin leer todo lo anterior.

Consecuencias fatales:

1. El coordinador tendría que **leer el archivo completo** para
   planificar — o sea, hacer secuencialmente el trabajo que quería
   paralelizar. El paralelismo se vuelve inútil: el cuello de botella se
   mueve al planificador.
2. Rompe el invariante 3 (el coordinador no lee contenido) y, con el
   backend `s3://`, significaría **descargar los 2 GB desde MinIO solo
   para contar saltos de línea**.
3. El worker no podría leer "solo su parte": no hay forma de expresar
   "la línea 100.001" como un `GET Range` HTTP. `Range` habla de bytes.

#### Opción B — particionar por **tamaño, sin más**

"Cada worker toma 32 MB y los parsea."

**El problema:** un corte en el byte 33.554.432 cae, con casi total
seguridad, **en medio de una línea**:

```
tramo 0 ──────────────────────────────────────────┤ byte 32MB
...1534 "-" "curl/8.1"\n192.168.1.45 - - [20/Oct/2026:14:3│2:11 -0300] "GET /api…
                       └──────── el tramo 0 ve esto ──────┘└── el tramo 1 ve esto ──┘
```

Resultado: **la línea partida se cuenta como dos registros inválidos**
(ninguna de las dos mitades matchea la regex). Con 64 tramos eso son 63
registros falsamente inválidos — y, peor, 63 registros **válidos
perdidos**. El total no cuadra con la referencia secuencial y el sistema
**no es correcto**.

#### Opción C — el **enfoque mixto** (el que usa LogHunter)

> **Se parte por tamaño (aritmética, barato, sin leer) y cada worker
> corrige los bordes de su propio tramo usando los saltos de línea.**

El coordinador sigue siendo puramente aritmético. El worker hace un
ajuste **local, de pocos bytes**, en sus dos extremos.

### 3.2 La regla que lo hace correcto

> **Una línea pertenece al tramo que contiene el offset de su PRIMER
> byte.**

Esa única regla resuelve todo, porque cada línea tiene **exactamente un**
primer byte, que cae en **exactamente un** tramo. La propiedad es
automáticamente **disjunta** (nada se cuenta dos veces) y **completa**
(nada se pierde).

De ahí salen las dos acciones del worker:

- **Borde izquierdo** (`_inicio_alineado`): si `inicio > 0`, mirar el
  byte `inicio - 1`.
  - Si es `\n` → `inicio` ya es el primer byte de una línea; arrancar ahí.
  - Si **no** es `\n` → `inicio` cae en el medio de una línea cuyo primer
    byte está en el tramo **anterior**. Esa línea **no es mía**: avanzar
    hasta pasar el próximo `\n` y empezar ahí.
- **Borde derecho** (`while cursor < fin`): la condición se evalúa sobre
  el **offset de inicio** de la línea. Si una línea empieza en
  `fin - 10` y mide 200 bytes, **es mía** y se lee **completa**, aunque
  eso cruce `fin`. El tramo siguiente la descartará por el borde
  izquierdo.

### 3.3 Por qué los dos mecanismos encajan sin huecos ni solapes

El punto que hay que ver es que **los dos bordes son el mismo criterio
visto desde los dos lados**:

| Situación en el corte `fin`/`inicio` | Tramo izquierdo | Tramo derecho | Resultado |
|---|---|---|---|
| El corte cae **exactamente después de un `\n`** | Termina ahí; la línea anterior ya se contó | Byte previo **es** `\n` → arranca en `inicio` | ✅ ni pérdida ni duplicado |
| El corte cae **en el medio de una línea** | La línea empezó antes de `fin` → **la lee completa**, cruzando el corte | Byte previo **no** es `\n` → **descarta** esa cola | ✅ contada una sola vez, por el izquierdo |

En ambos casos la línea se cuenta **una sola vez**. Y notá la simetría:
lo que un tramo lee de más, el siguiente lo descarta. Hay un test por
cada fila de esa tabla.

### 3.4 Casos borde que el diseño cubre (y están testeados)

| Caso borde | Qué pasa |
|---|---|
| Corte **entre el CR y el LF** de un CRLF | El tramo izquierdo lee hasta el LF (cruzándolo); el derecho ve que el byte previo es `\r`, no `\n`, y descarta. Correcto |
| Corte **sobre un carácter UTF-8 multibyte** | **No importa**: se particiona por bytes y se decodifica **después**, con la línea ya reconstruida completa. Nunca se decodifica media secuencia |
| **Última línea sin `\n` final** | `_leer_una_linea` corta también por EOF, y `parsear_linea` tolera la ausencia de salto. Se cuenta |
| **Línea más larga que 64 KiB** | `_leer_una_linea` acumula bloques de 64 KiB hasta el LF. El bloque es tamaño de I/O, **no** un límite de longitud |
| **Tramo sin ningún inicio de línea** (una línea gigante que lo cubre entero) | `_inicio_alineado` avanza más allá de `fin`, el `while cursor < fin` no entra, y el tramo devuelve un `Counter` vacío. Válido, no es un error |
| **Archivo vacío** | 0 tramos, 0 tareas, informe vacío, sin tocar Celery |
| **Archivo de una sola línea sin `\n`** | Un solo tramo la cuenta |

### 3.5 Ventajas y desventajas del enfoque mixto

**Ventajas:**

1. **La planificación es O(1).** Solo hace falta el tamaño
   (`fstat` o un `HEAD` HTTP). Cortar 2 GB en 64 tramos es una
   *list comprehension*, no una pasada por el archivo.
2. **El coordinador nunca toca el contenido** (invariante 3): no es
   cuello de botella y escala a archivos arbitrariamente grandes.
3. **Tramos de tamaño predecible** → carga balanceada entre workers.
   Particionar por líneas daría tramos de bytes muy desiguales si hay
   líneas de longitud variable.
4. **Compatible con `GET Range` HTTP** → un worker remoto baja **solo
   sus 32 MB**, nunca el archivo entero. Esto es lo que habilita la
   distribución real entre máquinas (ADR-0004).
5. **Idempotencia gratis**: `(uri, inicio, fin)` determina el resultado
   por completo. Un reintento da el mismo `Counter`, así que Celery puede
   reentregar tareas de workers caídos sin corromper nada.
6. **Sin preprocesamiento.** No hay que generar archivos de fragmentos
   (que duplicarían los datos en disco y habría que limpiar después), ni
   construir un índice de offsets de líneas (que habría que mantener y
   que exigiría una pasada completa igual).
7. **Determinista y verificable**: el resultado se compara byte a byte
   contra `calcular_referencia`, el oráculo secuencial.

**Desventajas (y hay que nombrarlas, porque muestra honestidad):**

1. **Hay lectura solapada.** Los tramos vecinos releen unos pocos bytes
   del otro (la cola de una línea partida). La afirmación correcta es:
   *los bytes leídos pueden solaparse, pero la **propiedad de los
   registros** es disjunta*. **Nunca digas "cada tarea lee exactamente
   `fin - inicio` bytes"** — no es cierto, y está explícito en ADR-0005.
2. **Una lectura extra de 1 byte por tramo** (el `leer(inicio-1, inicio)`
   del borde izquierdo). En `local://` es despreciable (page cache); en
   `s3://` es un `GET Range` adicional por tarea — 64 peticiones extra en
   un job de 64 tramos. Aceptable.
3. **El tramo puede quedar desbalanceado por una línea enorme.** Si una
   línea mide más que `chunk_mb`, el tramo que la posee hace más trabajo
   que el resto. No es incorrecto, solo desparejo.
4. **El archivo debe permanecer inmutable durante el job.** Si alguien le
   agrega líneas a mitad de camino, los offsets calculados dejan de
   corresponder. Es una precondición documentada, no un bug.
5. **No sirve para formatos comprimidos.** No se puede leer un rango de
   un `.gz` sin descomprimir todo lo anterior: por eso `protocolo.py`
   rechaza `.gz` con `UNSUPPORTED_URI`, explícitamente y de entrada.

### 3.6 El resumen de una frase

> **Se corta por bytes porque es lo único que se puede calcular sin leer
> el archivo; se respetan las líneas porque es lo único que da un
> resultado correcto. El enfoque mixto consigue las dos cosas poniendo el
> costo de la corrección donde es barato: unos pocos bytes en los bordes,
> en el worker, en paralelo.**

---

## Parte 4 — El flujo completo, de punta a punta

Seguimiento de un análisis real: `top-ips` sobre un log de 2 GiB en S3,
con `--chunk-mb 32` (⇒ 64 tramos) y 4 workers.

```
┌─ CLIENTE ──────────────────────────────────────────────────────────────┐
│ python -m loghunter.cliente -f s3://logs/access.log -q top-ips --top 10│
│                                                                        │
│ 1. argparse valida flags                          cliente.py           │
│ 2. _normalizar_archivo() → la URI s3:// queda igual                    │
│ 3. socket.create_connection(("127.0.0.1", 8888), timeout=10)           │
│ 4. sendall(b'{"version":1,"type":"analyze",…}\n') protocolo.codificar  │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ TCP 8888, JSON + LF
┌─ COORDINADOR (asyncio) ────────▼───────────────────────────────────────┐
│ 5. atender_cliente() ← nueva corrutina para esta conexión              │
│ 6. protocolo.leer_pedido() → readline + timeout 30s + límite 64KiB     │
│    validar_pedido(): versión, tipo, consulta, top, chunk_mb, URI       │
│ 7. Job(id=uuid4().hex, file=…, query="top-ips", top=10, chunk_mb=32)   │
│ 8. se lanzan DOS tareas que compiten:                                  │
│      _procesar_job()        ──┐                                        │
│      _esperar_desconexion() ──┴─ asyncio.wait(FIRST_COMPLETED)         │
│                                                                        │
│ 9. ¿hay cupo? Semaphore(3) → → "accepted" {state:"admitted"}           │
│10. await semaforo.acquire(); deadline = now + 300s                     │
│11. metricas.incrementar_jobs_activos()        ipc.py (Value+Lock)      │
│                                                                        │
│12. tamano = await asyncio.to_thread(_tamano_archivo, uri)              │
│         └──► lector.LectorS3 → HEAD HTTP → ContentLength = 2147483648  │
│13. rangos = calcular_rangos(2 GiB, 32 MiB) → 64 tramos (ARITMÉTICA)    │
│    → "started" {chunks:64, chunk_mb:32}                                │
│14. grupo = await asyncio.to_thread(analisis.lanzar_grupo, …)           │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ 64 × apply_async(uri, inicio, fin, query, job_id)
┌─ REDIS (broker, db 0) ─────────▼───────────────────────────────────────┐
│    64 tareas en cola — SOLO COORDENADAS, ~100 bytes cada una           │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ 4 procesos prefork consumen en PARALELO
┌─ WORKER (proceso 1 de 4) ──────▼───────────────────────────────────────┐
│15. tasks.map_chunk(uri, 33554432, 67108864, "top-ips", job_id)         │
│16.   workers.map_chunk(uri, 33554432, 67108864, "top-ips")             │
│17.     lector.abrir("s3://…") → LectorS3  (HEAD propio)                │
│18.     _inicio_alineado: leer(33554431, 33554432) → ¿es "\n"?          │
│          no → avanzar al próximo LF (descartar cola ajena)             │
│19.     while cursor < 67108864:                                        │
│          _leer_una_linea() → leer_hasta_salto() ── caché 512 KiB       │
│          parser.parsear_linea(linea) → Registro | None                 │
│20.     consultas.agregar_parcial("top-ips", registros) → Counter       │
│21.   return {contador, lineas, invalidas, worker:{hostname, pid}}      │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ Counter parcial (pocos KB)
┌─ REDIS (backend, db 1) ────────▼───────────────────────────────────────┐
│    64 resultados parciales                                             │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
┌─ COORDINADOR ──────────────────▼───────────────────────────────────────┐
│22. _esperar_grupo():                                                   │
│      corrutina _progreso()  cada 1s → _contar_completados → "progress" │
│      en paralelo: to_thread(reducir_resultados, grupo, restante, 0.5)  │
│        sondea cada AsyncResult: ready()? failed()? → cosecha           │
│        primer fallo definitivo → TareaFallida (NO espera al resto)     │
│        deadline vencido        → TimeoutError                          │
│23. consultas.reducir(parciales) → Counter TOTAL (suma de los 64)       │
│24. metricas.sumar_lineas_validas(n)                                    │
│25. consultas.presentar("top-ips", total, top=10) → el TOP, DESPUÉS     │
│26. _persistir_informe(): presentar(…, top=None) → agregado COMPLETO    │
│      await asyncio.to_thread(cola_informes.put, informe)               │
│27. → "result" {resultados:[…], lineas, invalidas, duracion, workers:4} │
│28. metricas.incrementar_informes_emitidos()                            │
│29. finally: decrementar_jobs_activos(); semaforo.release()  ← SIEMPRE  │
└───────┬────────────────────────────────────────────┬───────────────────┘
        │ multiprocessing.Queue                      │ TCP
┌─ INDEXADOR (proceso hijo) ─▼──────────┐   ┌────────▼───────────────────┐
│30. informe = cola.get()               │   │31. cliente: "result"       │
│31. guardar_informe(conn, informe)     │   │    _mostrar_informe()      │
│      with conn: SELECT job_id?        │   │    exit 0                  │
│                 INSERT informes       │   └────────────────────────────┘
│                 INSERT resultados ×N  │
│      → COMMIT (o ROLLBACK total)      │
│32. metricas.incrementar_informes_     │
│      persistidos()  ← solo si commitió│
└───────────────────────────────────────┘
```

### Dónde está cada concepto en ese flujo

| Paso | Concepto evaluable |
|---|---|
| 3–4 | **Socket cliente bloqueante** (sincrónico a propósito) |
| 5 | **Socket servidor concurrente** — una corrutina por cliente, un solo hilo |
| 6 | **Framing** por delimitador: TCP es un stream, no mensajes |
| 8 | **Carrera de tareas** con `asyncio.wait(FIRST_COMPLETED)` |
| 9–10 | **Control de admisión** con `asyncio.Semaphore` |
| 11, 24, 28, 32 | **IPC: memoria compartida** `Value` + `Lock` |
| 12, 14, 22, 26 | **`asyncio.to_thread`**: no bloquear el event loop |
| 13 | **Planificación aritmética**, sin leer contenido |
| 14 | **Cola de tareas distribuida**: por ahí viajan coordenadas |
| 15–21 | **Paralelismo real**: procesos `prefork`, posiblemente en otras máquinas |
| 18–19 | **Particionado por bytes con alineación a línea** |
| 22 | **Dos corrutinas concurrentes**: progreso + reduce |
| 23, 25 | **Reduce**: suma de `Counter`; el top **después** |
| 26, 30 | **IPC: paso de mensajes** por `multiprocessing.Queue` |
| 31 | **Único escritor de SQLite**, transacción atómica |

---

## Parte 5 — Los tests: estrategia y mapa

**217 tests en 20 archivos.** Lo que importa para la defensa no es el
número: es que hay una **estrategia** detrás, con tres niveles claros.

### 5.1 Los tres niveles

| Nivel | Qué prueba | Necesita infraestructura | Archivos |
|---|---|---|---|
| **Unitario** | Una función/clase aislada, con dobles donde hace falta | No | `test_parser`, `test_consultas`, `test_lector`, `test_lector_s3`, `test_workers`, `test_protocolo`, `test_generar`, `test_ipc`, `test_indexador`, `test_tasks`, `test_analisis`, `test_cliente`, `test_imports` |
| **Integración con dobles** | Varias capas juntas, controlando el tiempo con `threading.Event` | No | `test_coordinador`, `test_coordinador_persistencia`, `test_operacion`, `test_integracion_nucleo` |
| **Integración real** | El sistema completo con Redis, MinIO y workers Celery de verdad | **Sí** — si no está, se **saltan** | `test_integracion_distribucion`, `test_integracion_cliente_servidor`, `test_fallos` |

### 5.2 La decisión más importante de la estrategia de pruebas

> **Los tests que requieren infraestructura real se SALTAN si no la
> encuentran, en vez de simularla.**

El decorador `@requiere_infraestructura_real` chequea si Redis/MinIO
responden en `localhost` y, si no, hace `pytest.skip` **con un motivo
explícito**. El razonamiento está en el contrato
(ADR-0013): *"los mocks por sí solos no demuestran
distribución"*. Un mock de Celery probaría que el código llama a Celery
como esperamos, no que el trabajo se reparte de verdad entre procesos en
máquinas distintas. Para eso hace falta infraestructura real.

Los tres escenarios están medidos (ver
[OPERACION.md](OPERACION.md#modos-de-prueba-pytest)):

| Infraestructura disponible | Resultado real |
|---|---|
| Nada | `201 passed, 16 skipped` |
| Redis + MinIO, sin worker propio | `205 passed, 12 skipped` |
| Redis + MinIO + worker standalone | `217 passed, 0 skipped` |

### 5.3 La técnica de dobles controlables por evento

El patrón que hace posible probar el coordinador sin Celery. El
`Coordinador` acepta `lanzar_grupo`, `reducir_resultados`,
`cola_informes` y `metricas` **por constructor**, así que los tests
inyectan dobles (`_GrupoFalso`, `_ResultadoFalso`, `_Control`,
`_ColaFalsa`) cuya terminación se dispara con un `threading.Event`.

Eso permite escribir cosas que con infraestructura real serían
imposibles de reproducir en forma determinista: *"largá el job A, no
dejes que termine, largá el job B, hacé que B falle, verificá que A
sigue corriendo y que el cupo de B se liberó"*. Nada de `sleep` y
esperanza.

### 5.4 La verificación por oráculo

La técnica central del proyecto, y la que contestaría cualquier duda
sobre si el particionado es correcto:

```
consultas.calcular_referencia(archivo, query)     ← un proceso, secuencial, sin fragmentar
                    ==
reducir(map_chunk(uri, i, f, query) for i, f in calcular_rangos(tamano, chunk))
```

`test_integracion_nucleo.py` lo corre con tramos de 64 KiB, 1 MiB y
**700.003 bytes** (un número deliberadamente primo y no alineado a nada,
para que los cortes caigan en lugares incómodos), y hasta con **tramos
de un byte**. `test_integracion_distribucion.py` lo repite con Celery,
Redis y MinIO reales, para las 4 consultas × 2 tamaños de tramo, y
verifica además que **`local://` y `s3://` dan el mismo resultado**.

### 5.5 `test_fallos.py` — lo que los dobles no pueden demostrar

4 tests que apagan infraestructura de verdad (`docker stop`) con el
coordinador real, su indexador hijo real y workers Celery reales:

- un **fallo definitivo de tarea no afecta a un job concurrente sano**;
- **MinIO caído** produce un error finito (reintentos acotados, no espera
  infinita) y el coordinador **sigue atendiendo**;
- **Redis caído** al publicar → `BROKER_UNAVAILABLE`;
- un **timeout real no cancela un job concurrente** y libera su cupo.

Nota de honestidad que vale conocer: la **pérdida real de un ejecutor a
mitad de job** se verificó **manualmente** en vez de automatizarse. Si te
preguntan si está todo automatizado, la respuesta correcta es esa, no un
"sí".

### 5.6 `test_imports.py` — el test más raro y más inteligente

Un solo test: lanza un **subproceso** desde un directorio distinto al
del repo, parchea `socket`, `sqlite3`, `subprocess`, `threading` y
`multiprocessing.Process` para que **lancen excepción si se usan**, y
después importa todos los módulos de `loghunter`.

Qué demuestra: **importar el paquete no abre sockets, no conecta a
Redis, no crea procesos y no abre SQLite.** Eso es lo que hace seguro
que `coordinador.py` importe `protocolo` o `lector` sin efectos
colaterales, y es la razón de que `__init__.py` esté vacío y de que
`_cargar_analisis()` haga el import de Celery **tardío**.

---

## Parte 6 — Infraestructura, empaquetado y medición

### `pyproject.toml`

Paquete `loghunter` con layout `src/`, Python `>=3.12,<3.13` (fijado:
no se promete compatibilidad con otras versiones). Dependencias de
producción: **solo dos** — `boto3` y `celery[redis]`. Todo lo demás es
stdlib. Dev: `pytest`. `addopts = "-ra --import-mode=importlib"` y
`testpaths = ["tests"]`.

`uv.lock` fija las versiones **exactas** de todo el árbol: `uv sync
--locked` reproduce el entorno bit a bit. Eso es lo que permite afirmar
que un resultado medido hoy se reproduce mañana.

### `Dockerfile`

**Una sola imagen para coordinador y worker.** El rol lo decide el
`command:` del Compose, no la imagen. `python:3.12-slim`, instala las
dos dependencias y el paquete en modo editable. El `CMD` por default es
el coordinador.

Comentario explícito en el archivo: **el indexador no tiene imagen
propia** — es un proceso hijo que el coordinador lanza con
`multiprocessing` dentro de su mismo contenedor, no un quinto servicio.
Eso es coherente con el diseño: es IPC local, no un microservicio.

### `docker-compose.yml`

Cuatro servicios: `redis`, `minio`, `coordinador`, `worker`. Los cuatro
con **healthcheck** y `depends_on: condition: service_healthy`, así el
coordinador no arranca antes de que Redis y MinIO estén listos.
Volúmenes nombrados (`minio-data`, `sqlite-data`) para que el bucket y
el histórico sobrevivan a un `docker compose down` (sin `-v`).

El healthcheck del worker es `celery -A loghunter.tasks inspect ping |
grep -q OK`: no basta con que el proceso exista, tiene que **responder
al broker**.

`docker compose up -d --scale worker=4` replica el **contenedor** worker.
Con `-c 4` dentro de cada réplica son **16 procesos** Celery. **Dos ejes
distintos** — y ninguno de los dos simula varias *máquinas*: para eso
hace falta un worker externo apuntando al mismo Redis/MinIO (hay
procedimiento en `INSTALL.md`, ejecutado de verdad).

### `scripts/benchmark_procesos.py`

Medición real del efecto del paralelismo. No corre con pytest: es una
medición puntual con infraestructura real; sus resultados registrados
están en OPERACION.md §4.8.

Qué hace: genera un log de 48 MB con semilla fija (2026), lanza el
coordinador con `--chunk-mb 1` (⇒ 48 tramos) y `--max-jobs 1`, y para
cada configuración `-c {1,2,4}` lanza un worker, espera a que responda
al `ping`, corre el cliente **3 veces** con `--raw`, toma la **mediana**
y calcula el speedup. **Y en cada corrida verifica con un `assert` que
el agregado es idéntico** al de la primera — no solo que fue más rápido.

**Los resultados reales:**

| Procesos (`-c`) | Mediana (s) | `speedup = T(1)/T(p)` | PIDs distintos observados |
|---|---|---|---|
| 1 | 8.771 | 1.000 | 1 |
| 2 | 5.186 | 1.691 | 2 |
| 4 | 2.635 | **3.328** | 4 |

`lineas=456552` e `invalidas=9424` **idénticos en las nueve corridas**.

Cómo presentarlo con precisión: el speedup es **sublineal** (3.33 contra
el ideal 4.0), lo cual es lo esperable — hay costo fijo por tarea
(publicar en Redis, el `HEAD`/`fstat` por tarea, serializar el parcial)
que no se paraleliza. **No se promete que 8 o 16 procesos sigan
mejorando en la misma proporción.** Esa es la lectura honesta: la
medición demuestra que el paralelismo funciona y da una aceleración
sustancial, no una ley de escalado universal.

---

## Parte 7 — Preguntas probables, con la respuesta corta

**¿Cuál es la diferencia entre concurrencia y paralelismo, y dónde está cada una acá?**
Concurrencia es tener varias tareas en progreso intercaladas (sirve para
esperar); paralelismo es ejecutarlas literalmente a la vez (sirve para
calcular). Concurrencia: `asyncio` en `coordinador.py`. Paralelismo:
procesos Celery `prefork` en `tasks.py`/`workers.py`.

**¿Por qué dos modelos y no uno solo?**
Porque los dos componentes tienen perfiles opuestos. El coordinador es
I/O-bound (solo espera) → `asyncio` es lo más barato y no tiene *data
races*. Los workers son CPU-bound (regex sobre millones de líneas) → el
GIL impide paralelizar cálculo con hilos, así que hacen falta procesos.

**¿Por qué no hilos en los workers?**
El GIL: dos hilos no ejecutan bytecode Python a la vez, así que no habría
paralelismo real de cálculo.

**¿Qué viaja por la cola?**
Coordenadas: `(uri, inicio, fin, query, job_id)`. Unos cien bytes.
**Nunca los bytes del tramo** — serían GB por Redis. El worker resuelve
su propio acceso vía `lector.py`, y **eso** es lo que permite que viva
en otra máquina.

**¿Cómo se parte el archivo?**
Por **tamaño**, con aritmética pura sobre el tamaño total, sin leer
contenido. Cada worker **alinea los bordes de su propio tramo** a fin de
línea. Una línea pertenece al tramo que contiene el offset de su primer
byte.

**¿Y si un corte cae en medio de una línea?**
El tramo izquierdo la lee **completa** (empezó antes de `fin`); el
derecho detecta que el byte previo a su `inicio` no es `\n` y **descarta
esa cola**. Se cuenta una sola vez.

**¿Por qué no particionar por líneas?**
Porque para saber dónde empieza la línea N hay que leer todo lo
anterior. El coordinador tendría que leer el archivo completo — haría
secuencialmente el trabajo que quería paralelizar. Y `GET Range` HTTP
habla de bytes, no de líneas.

**¿Cómo se garantiza que no se pierden ni se duplican líneas?**
Por la regla de propiedad por primer byte (disjunta y completa por
construcción), y se **verifica** comparando contra
`calcular_referencia`, que procesa el archivo secuencialmente en un solo
proceso.

**¿Qué pasa si se cae un worker a mitad de un tramo?**
`task_acks_late=True` hace que la tarea se confirme **al terminar**, no
al recibirla; como nunca se confirmó, el broker la **reentrega** a otro
worker. Y como `map_chunk` es **idempotente**, el reintento recalcula el
mismo tramo y da el mismo `Counter`. Se asume posible re-ejecución, **no
ejecución exactamente una vez**.

**¿Qué pasa si se cae Redis?**
Si se cae al publicar → `BROKER_UNAVAILABLE`, y si la publicación quedó a
mitad se **revocan** las tareas ya publicadas. Si se cae durante el
reduce → error de infraestructura acotado. Redis es un punto único de
fallo reconocido: no hay clúster HA, está en las consecuencias de
ADR-0003.

**¿Qué pasa si se cae MinIO?**
`LectorS3` reintenta **finitamente** (3 veces, backoff 0.2/0.4/0.8 s) y
**solo** los errores transitorios (5xx, `SlowDown`, timeouts). Un 404 o
un 403 fallan **al instante**: reintentar no los va a arreglar. Agotados
los reintentos → `STORAGE_UNAVAILABLE`, y el coordinador sigue atendiendo.

**¿Qué pasa si se cae el indexador?**
`_supervisar_indexador` lo detecta en ≤2 s con `is_alive()`, deja de
admitir análisis nuevos con `SERVICE_UNAVAILABLE`, y un job ya en curso
falla con el mismo código al intentar persistir. **Mejor fallar que
anunciar un éxito que no se puede honrar.**

**¿Por qué el `top` se calcula al final y no en cada worker?**
Porque el top local **no** determina el top global: una IP que es la #11
en cada uno de 64 tramos puede ser la #1 del archivo. Cada worker
devuelve su `Counter` completo y el recorte se aplica después del reduce.
Hay un test que lo demuestra.

**¿Por qué JSON y no `pickle`?**
`pickle` **ejecuta código al deserializar** y el coordinador escucha en
`0.0.0.0` aceptando conexiones de cualquiera. Sería ejecución remota de
código.

**¿Por qué hace falta un `Lock` si `asyncio` es monohilo?**
Son dos problemas distintos. El `asyncio.Lock` del coordinador protege
**carreras lógicas** que cruzan un `await` (el patrón *leer → `await` →
escribir*); `asyncio` elimina las *data races*, no estas. El
`multiprocessing.Lock` de `ipc.py` protege contra **concurrencia real
del SO** entre procesos: `+= 1` son tres operaciones (leer, sumar,
escribir) y sin lock se pierden incrementos.

**¿Por qué SQLite y no PostgreSQL?**
Porque hay **un único escritor** (el proceso indexador). Serializando
toda la escritura en un proceso, SQLite alcanza y no hay un servidor que
administrar. Si hubiera varios escritores, SQLite no sería defendible.

**¿Por qué el indexador es un proceso aparte?**
Para no bloquear el event loop con `sqlite3` (invariante 1) y para ser
el único escritor (invariante 7).

**¿Para qué sirve `SIGUSR1`?**
Para pedir un snapshot del estado (jobs activos con su progreso, y los
cuatro contadores) sin reiniciar nada y sin construir una API de
administración. El handler **solo agenda** una corrutina; no hace I/O.

**¿Qué pasa con Ctrl-C?**
`SIGINT`/`SIGTERM` → `_apagar()`: se deja de aceptar conexiones, se
notifica `SERVER_SHUTDOWN` a los clientes activos y se cancelan sus
tareas, se encola el **centinela** `None` después de todo lo pendiente,
y se espera al hijo con `join(30s)` fuera del loop. Si no termina, se
loguea que **no se garantiza drenaje completo** y se hace `terminate()`.
El hijo **ignora `SIGINT`** para que el cierre lo organice el padre y no
se pierdan informes.

**¿Cuántos clientes atiende a la vez?**
Conexiones, las que entren (una corrutina por cada una). **Análisis
simultáneos**, `--max-jobs` (default 3), por el `Semaphore`. Un pedido
sin cupo recibe `accepted` con `state: queued` y espera **sin publicar
tareas**.

**¿El `--job-timeout` cuenta el tiempo en cola?**
No. El deadline arranca **al adquirir el cupo**.

**¿Qué `speedup` se midió?**
3.33× con 4 procesos contra 1 (8.771 s → 2.635 s), con agregado idéntico
en las nueve corridas. Sublineal por el costo fijo por tarea; **no** se
promete que la proporción se mantenga con 8 o 16.

**¿Dónde está la evidencia de que el sistema realmente distribuye?**
El informe reporta `workers observados`, que es
`len({(hostname, pid)})` de los parciales — procesos distintos que
participaron de verdad. El procedimiento reproducible —sumar un worker
sin acceso al filesystem del log y verlo tomar tramos por `s3://`— está
en OPERACION.md §4.4, con la salida real donde el conteo pasa de 8 a 10.

---

## Parte 8 — Chuleta de una página

```
ARQUITECTURA   cliente(TCP/JSON) → coordinador(asyncio) → Redis → workers(procesos) → MinIO/local
                                         ↓ mp.Queue
                                   indexador(hijo) → SQLite

CONCURRENCIA   asyncio          coordinador.py   I/O-bound: esperar N sockets con 1 hilo
PARALELISMO    Celery prefork   tasks.py         CPU-bound: el GIL obliga a usar procesos
IPC            mp.Queue         coordinador↔indexador (informes, centinela None)
               Value+Lock       ipc.py (4 contadores; += NO es atómico)
SEÑALES        SIGINT/SIGTERM   apagado ordenado; SIGUSR1 snapshot; el handler solo agenda
HILOS          to_thread        saca del loop lo bloqueante: Celery, boto3, sqlite, Queue.put

PARTICIONADO   por BYTES (aritmética, O(1), sin leer) + alineación a LF en el worker
REGLA          una línea pertenece al tramo que contiene su PRIMER byte
               → propiedad disjunta y completa; los BYTES leídos sí se solapan
IDEMPOTENCIA   map_chunk solo depende de (uri, inicio, fin, query) → reintento seguro
REDUCE         suma de Counter (asociativa y conmutativa) → el TOP se aplica al FINAL

PROTOCOLO      JSON + LF (no pickle: ejecuta código). 64 KiB / 30 s de límite
MENSAJES       accepted → started → progress* → result | error
CÓDIGOS        INVALID_REQUEST UNSUPPORTED_QUERY UNSUPPORTED_URI FILE_NOT_FOUND
               FILE_ACCESS_ERROR STORAGE_UNAVAILABLE BROKER_UNAVAILABLE TASK_FAILED
               JOB_TIMEOUT SERVICE_UNAVAILABLE SERVER_SHUTDOWN INTERNAL_ERROR

ADMISIÓN       Semaphore(--max-jobs=3); sin cupo → accepted{queued}, sin publicar nada
DEADLINE       --job-timeout=300 s desde que se ADQUIERE el cupo (no desde que conecta)
RECUPERACIÓN   acks_late + reject_on_worker_lost + idempotencia = reentrega segura
               reintentos S3 finitos (3, backoff 0.2/0.4/0.8) solo si es transitorio
AISLAMIENTO    job_id + lista propia de AsyncResult; revoke() tarea por tarea, nunca global

CONSULTAS      top-ips(IP,+1) status(código,+1) urls(destino,+1) traffic(hora UTC,+bytes)
--top          recorta SOLO top-ips y urls; desempate por clave ascendente (determinista)

NÚMEROS        217 tests · 12 módulos · 15 ADR · 24 casos de uso
               speedup 3.33× con 4 procesos (8.771 s → 2.635 s), agregado idéntico
               2 GiB / 32 MiB = 64 tramos · chunk 1–256 MiB · caché S3 512 KiB
```

---

## Referencias

- [OPERACION.md](OPERACION.md) — cómo ejecutar cada cosa, defaults de CLI, escalado, problemas comunes
- [Índice de ADR](../adr/INDEX.md) — en particular ADR-0002 (asyncio), ADR-0003 (Celery/Redis), ADR-0004 (lector local/S3), ADR-0005 (particionado y líneas), ADR-0006 (protocolo), ADR-0007 (admisión y timeout), ADR-0008 (indexador/SQLite), ADR-0009 (IPC y señales)
- [INFO.md](../../INFO.md) — decisiones de diseño y su justificación
- [INSTALL.md](../../INSTALL.md) — entorno, servicios, Compose, worker remoto real
- [README.md](../../README.md) — propósito del proyecto y arquitectura de un vistazo
- `CLAUDE.md` — los nueve invariantes de diseño

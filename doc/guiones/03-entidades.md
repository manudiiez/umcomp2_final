# 3. Funcionalidades de cada entidad

## 3.1. `cliente.py` — cliente CLI

**Responsabilidad:** traducir una invocación de línea de comandos en un pedido al
coordinador, y presentar el informe recibido.

**Funcionalidades**

1. Parsear argumentos con `argparse` y validarlos antes de abrir el socket
   (que la consulta exista, que `--top` sea positivo, que el puerto sea válido).
2. Normalizar la referencia al log a una **URI completa** (`local:///ruta/abs` o
   `s3://bucket/objeto`), porque el coordinador y los workers la van a resolver desde
   máquinas distintas. Una ruta suelta se interpreta como `local://` y se vuelve
   absoluta.
3. Conectarse por TCP al coordinador, detectando si la dirección es IPv4 o IPv6
   para elegir la familia de socket.
4. Enviar el pedido como una línea JSON.
5. Consumir los mensajes de progreso y mostrar una barra que se actualiza en vivo.
6. Recibir el informe final y formatearlo como tabla legible en consola.
7. Informar errores del servidor (archivo inexistente, consulta desconocida,
   servidor saturado) con mensaje claro y código de salida distinto de cero.
8. Soportar `--raw` para emitir el JSON crudo, de modo que la salida sea
   encadenable con otras herramientas.

**Argumentos**

```
-i, --ip        dirección del coordinador
-p, --port      puerto del coordinador
-f, --file      URI del log a analizar (local:///... o s3://bucket/objeto)
-q, --query     top-ips | status | urls | traffic
    --top       cantidad de resultados a mostrar (default 10)
    --chunk-mb  tamaño de tramo sugerido (default 32)
    --raw       emitir JSON sin formatear
```

**No hace:** no lee el log, no parsea líneas, no habla con Redis.

---

## 3.2. `coordinador.py` — servidor asyncio

**Responsabilidad:** aceptar clientes concurrentes, partir el trabajo, despacharlo
y recomponer el resultado. Es el único nodo con estado.

**Funcionalidades**

1. Levantar un servidor TCP con `asyncio.start_server`, atendiendo **N clientes
   concurrentes** en un solo hilo.
2. Validar cada pedido: que el archivo exista y sea legible, que la consulta esté
   soportada, que el tamaño de tramo sea razonable.
3. Adquirir un `asyncio.Semaphore` con `--max-jobs` permisos antes de iniciar un
   análisis, para no saturar Redis ni la memoria con análisis simultáneos. Si está
   lleno, el cliente espera en cola (y se le informa).
4. **Calcular los cortes**: pedir el tamaño del archivo al `lector`
   (`HEAD` en S3, `os.stat` en local) y producir la lista de pares `(inicio, fin)`
   por aritmética sobre `--chunk-mb`. **No lee el contenido del log**: la alineación
   a fin de línea la hace cada worker sobre su tramo.
5. Asignarle al pedido un **`job_id`** (`uuid4().hex`) y registrarlo en el
   diccionario de jobs activos junto al `writer` del cliente, la URI, la consulta,
   la hora de inicio y el `GroupResult`.
6. Despachar un `group` de tareas `map_chunk`, una por tramo, con `apply_async`, y
   quedarse con el `GroupResult` que lo identifica.
7. Esperar los parciales **sin bloquear el event loop**, delegando el `.get()`
   bloqueante de Celery a un hilo con `asyncio.to_thread`.
8. Emitir progreso incremental al cliente leyendo `GroupResult.completed_count()`,
   y considerar el análisis terminado cuando iguala la cantidad de cortes.
9. Aplicar un **`--job-timeout`** por análisis: si vence, revocar las tareas
   pendientes de *ese* `GroupResult`, informarle el error a *ese* cliente y liberar
   el `Semaphore`, sin afectar a los demás.
10. **Hacer el reduce**: fusionar los `Counter` parciales en el agregado final,
    recortar al `--top` pedido y sumar los totales de líneas válidas e inválidas.
11. Depositar el informe en la `multiprocessing.Queue` del indexador y seguir
    adelante sin esperar la escritura en disco.
12. Mantener los contadores compartidos (`Value` + `Lock`): jobs activos, informes
    emitidos, líneas procesadas.
13. **No ceder el control dentro de una sección crítica**: ninguna secuencia
    *leer → `await` → escribir* sobre el diccionario de jobs o los contadores de
    progreso. Donde la sección crítica deba esperar, protegerla con un
    `asyncio.Lock`.
14. Lanzar y supervisar el proceso hijo indexador al arrancar.
15. Manejar señales: `SIGUSR1` vuelca por stdout los jobs activos con su progreso;
    `SIGINT`/`SIGTERM` dejan de aceptar clientes, revocan las tareas en vuelo,
    cierran la Queue, esperan al indexador con `wait()` y salen.
16. Registrar errores de tareas y propagarlos al cliente correspondiente sin
    afectar a los demás clientes conectados.

**Argumentos**

```
-i, --ip          interfaz de escucha (default 0.0.0.0)
-p, --port        puerto de escucha (default 8888)
    --chunk-mb    tamaño de tramo por defecto
    --max-jobs    análisis pesados simultáneos permitidos (default 3)
    --job-timeout segundos máximos por análisis (default 300)
    --broker      URL de Redis (default redis://localhost:6379/0)
    --s3-endpoint URL de MinIO (default http://localhost:9000)
    --db          ruta del SQLite
```

**No hace:** no parsea líneas de log y **no lee el contenido del archivo**: lo
único que le pide al almacenamiento es el tamaño.

---

## 3.3. `tasks.py` y los workers Celery — el *map*

**Responsabilidad:** procesar un tramo del archivo y devolver un contador parcial.
Sin estado y reemplazables.

**Funcionalidades**

1. Exponer la tarea `map_chunk(uri, inicio, fin, query, job_id)`.
2. Pedirle al `lector` **únicamente** el rango `[inicio, fin)`: `GET Range` si la
   URI es `s3://`, `mmap` si es `local://`. Nunca el archivo completo.
3. **Alinear el tramo a fin de línea** por su cuenta: si `inicio > 0`, descartar
   todo hasta el primer `\n` inclusive (esa línea es del tramo anterior); y si la
   última línea quedó cortada, pedir una cola de 64 KB más allá de `fin` para
   completarla. Así los tramos quedan disjuntos sin que nadie coordine nada.
4. Parsear cada línea con la expresión regular del formato *combined*, extrayendo
   IP, timestamp, método, URL, código de estado y bytes.
5. Acumular en un `collections.Counter` según la consulta:
   - `top-ips` → clave: IP
   - `status` → clave: código HTTP
   - `urls` → clave: ruta pedida
   - `traffic` → clave: hora truncada; valor: suma de bytes
6. Contar las líneas inválidas sin abortar el tramo.
7. Devolver `{"contador": {...}, "lineas": n, "invalidas": m}`.
8. Ser **idempotente**: reprocesar el mismo tramo da el mismo resultado, lo que
   hace seguro el reintento automático de Celery ante caída de un worker.
9. Declarar un `time_limit` para que un tramo patológico no bloquee al worker
   indefinidamente.
10. Reintentar la lectura del almacenamiento con *backoff* si MinIO no responde: es
    I/O de red y puede fallar transitoriamente.

**Operación**

```bash
celery -A loghunter.tasks worker --loglevel=INFO -c 4
```

El paralelismo se escala lanzando más workers o subiendo `-c`, sin reiniciar el
coordinador. **Los workers pueden correr en máquinas distintas** de la del
coordinador y de la del log: solo necesitan alcanzar la URL de Redis y la de MinIO.

```bash
# worker en otra máquina
celery -A loghunter.tasks worker -c 4 \
       --broker redis://coordinador:6379/0
# con S3_ENDPOINT=http://almacenamiento:9000 en el entorno
```

**No hace:** no conoce a los clientes, no escribe en la base, no decide los cortes
ni lee el archivo completo. Recibe coordenadas y devuelve números.

---

## 3.4. `lector.py` — acceso al archivo por rango de bytes

**Responsabilidad:** dar a cualquier proceso un pedazo de un archivo —identificado
por una URI y dos offsets— sin que le importe dónde está ese archivo. Es el módulo
que desacopla a los workers de la máquina del log.

**Interfaz**

```python
lector = abrir(uri)          # elige el backend según el esquema de la URI
lector.tamano()              # int: bytes totales
lector.leer(inicio, fin)     # bytes: solo ese rango
```

**Funcionalidades**

1. Elegir la implementación según el esquema de la URI: `local://` o `s3://`.
2. **Backend `local://`**: `os.stat().st_size` para el tamaño y `mmap` para el
   rango, con páginas por demanda y sin copia a espacio de usuario. Los workers del
   mismo host comparten las páginas físicas de la caché.
3. **Backend `s3://`**: `HEAD` del objeto para el tamaño (`Content-Length`) y `GET`
   con cabecera `Range: bytes=inicio-fin` para el rango, vía `boto3` apuntando al
   endpoint de MinIO.
4. Exponer `leer_hasta_salto(desde, max_bytes)` para que el worker pueda completar
   su última línea sin traerse más de lo necesario.
5. Reintentar con *backoff* los errores transitorios del backend remoto.
6. Validar que el rango pedido esté dentro del archivo y fallar con un error claro
   si no.

**Por qué existe como módulo aparte:** el worker no debe saber si el log está en un
disco local o en un bucket. Eso permite tener el camino eficiente con `mmap` para
una sola máquina y el camino escalable con `Range` requests para varias, sin
duplicar la lógica de conteo ni tocar `tasks.py`.

**No hace:** no parsea, no decide los cortes, no cachea tramos entre tareas.

---

## 3.5. `indexador.py` — proceso hijo de persistencia

**Responsabilidad:** ser el único escritor de la base de datos.

**Funcionalidades**

1. Arrancar como proceso hijo del coordinador, recibiendo la `Queue` y los
   `Value` compartidos.
2. Crear el esquema SQLite si no existe.
3. Consumir informes de la `Queue` en bucle bloqueante.
4. Insertar cada informe en `informes` y sus pares clave/valor en `resultados`,
   en una única transacción.
5. Actualizar los contadores compartidos bajo `Lock`.
6. Terminar ordenadamente al recibir el centinela de fin de cola o `SIGTERM`,
   cerrando la conexión SQLite con los datos comprometidos.

**Por qué es un proceso aparte y no una corrutina:** `sqlite3` es una biblioteca
bloqueante. Un `INSERT` que espera un `fsync` dentro del event loop congelaría la
atención de **todos** los clientes. Aislarlo en un proceso con una cola de por
medio hace que la latencia de disco sea invisible para el camino crítico.

---

## 3.6. `generar.py` — generador de logs sintéticos

**Responsabilidad:** producir archivos de prueba de tamaño y características
controladas.

**Funcionalidades**

1. Escribir un log en formato *combined* hasta alcanzar el tamaño pedido, en disco
   local o subiéndolo al bucket de MinIO según el esquema de `--out`.
2. Sortear IPs de un conjunto de cardinalidad configurable, para que `top-ips`
   tenga una distribución interesante.
3. Usar una distribución realista de códigos HTTP (mayoría 200, algo de 404, pocos
   500) y de tamaños de respuesta.
4. Generar timestamps monótonos con picos de tráfico en ciertas horas, para que
   `traffic` muestre algo.
5. Inyectar líneas corruptas según `--corrupt-pct`, para ejercitar el conteo de
   inválidas.
6. Informar por pantalla el tamaño final y la cantidad de líneas escritas.

**Argumentos**

```
-o, --out           destino: ruta local o s3://bucket/objeto
    --size-mb       tamaño objetivo en MB
    --ips           cantidad de IPs distintas (default 5000)
    --corrupt-pct   porcentaje de líneas corruptas (default 0)
    --seed          semilla, para reproducibilidad
```

`--seed` es lo que permite que la medición de *speedup* compare exactamente el
mismo archivo entre corridas.

---

## 3.7. Redis — broker y backend

**Responsabilidad:** transportar tareas y resultados entre coordinador y workers.

**Funcionalidades**

1. Como **broker**, mantener la cola de tareas pendientes. Si no hay workers
   vivos, las tareas se acumulan y se procesan cuando alguno aparece.
2. Como **backend**, almacenar los resultados parciales hasta que el coordinador
   los recoge.
3. Permitir que los workers estén en otras máquinas: es la única pieza que cruza
   la frontera del host.

Es un servicio de infraestructura: no se programa, se despliega (`docker run -d -p
6379:6379 redis`). Se elige Redis en lugar de RabbitMQ por simplicidad de
despliegue y porque cubre broker y backend con un solo servicio, siguiendo lo
visto en clase.

---

## 3.8. MinIO — almacenamiento del log

**Responsabilidad:** alojar los archivos de log y servir rangos de bytes a quien los
pida, desde cualquier máquina.

**Funcionalidades**

1. Exponer una **API compatible con S3** sobre HTTP (puerto 9000), con lo que
   cualquier cliente estándar (`boto3`, `aws-cli`, `mc`) puede leer y escribir.
2. Responder **`GET` con `Range: bytes=inicio-fin`** devolviendo únicamente ese
   fragmento del objeto, que es lo que hace posible el diseño de coordenadas.
3. Responder `HEAD` con el tamaño del objeto, que es todo lo que el coordinador
   necesita para calcular los cortes.
4. Recibir los logs que sube `generar.py`.

Es un servicio de infraestructura: no se programa, se despliega (una imagen de
Docker en el `docker-compose`). Se elige MinIO por ser una implementación libre de
la API S3; el código no depende de MinIO en particular, sino del protocolo.

**Por qué no un volumen compartido o NFS:** un volumen compartido obliga a que los
workers corran en la máquina del log —que es el servidor web— y eso impide escalar
horizontalmente. NFS lo resolvería sin cambiar código, pero exige administrar un
servidor NFS y la coherencia de caché de `mmap` sobre NFS es frágil. La API S3 es un
estándar y sus `Range` requests expresan exactamente el diseño del proyecto.

---

## 3.9. SQLite — almacenamiento

**Responsabilidad:** conservar el histórico de análisis.

**Funcionalidades**

1. Guardar un registro por análisis con sus métricas de ejecución.
2. Guardar los pares clave/valor del resultado agregado.
3. Permitir consultar el histórico y comparar duraciones entre corridas con
   distinta cantidad de workers.

---

## 3.10. Matriz entidad × mecanismo

| | Sockets TCP | asyncio | `mmap` | API S3 | `Queue` | `Value`+`Lock` | Señales | Celery | SQLite | `argparse` |
|---|---|---|---|---|---|---|---|---|---|---|
| `cliente.py` | ✓ | | | | | | | | | ✓ |
| `coordinador.py` | ✓ | ✓ | | ✓ (solo `HEAD`) | ✓ | ✓ | ✓ | ✓ | | ✓ |
| `lector.py` | | | ✓ | ✓ | | | | | | |
| worker Celery | | | ✓ (vía lector) | ✓ (vía lector) | | | | ✓ | | |
| `indexador.py` | | | | | ✓ | ✓ | ✓ | | ✓ | |
| `generar.py` | | | | ✓ | | | | | | ✓ |

> El coordinador ya no usa `mmap`: después del cambio de almacenamiento solo pide el
> **tamaño** del archivo, nunca su contenido. `mmap` queda como backend local del
> `lector`, que es donde aporta como mecanismo de IPC por memoria compartida.

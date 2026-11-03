# 1. Descripción verbal de la aplicación

## 1.1. El problema

Los servidores web producen archivos de log que crecen sin límite. Un `access.log`
de Nginx de un sitio con tráfico moderado alcanza fácilmente los 2 GB y los 18
millones de líneas en pocas semanas.

Responder preguntas operativas sobre ese archivo —*¿qué IPs están generando más
tráfico?*, *¿cuántos 500 estamos devolviendo?*, *¿cuáles son las URLs más
pedidas?*, *¿a qué hora se produjo el pico?*— con herramientas secuenciales
(`grep | sort | uniq -c | sort -rn`, o un script Python que recorre el archivo
línea por línea) tiene dos problemas:

1. **Es lento.** Un único proceso recorre el archivo a la velocidad de un solo
   núcleo. En la máquina de desarrollo, procesar 2 GB toma del orden de dos
   minutos y medio, durante los cuales no se puede hacer otra cosa.
2. **No escala ni se comparte.** El cómputo queda atado a la máquina y al momento
   en que se lanzó. No hay forma de sumar capacidad, ni de que varias personas
   consulten el mismo archivo a la vez, ni queda registro de lo que se consultó.

El trabajo es **vergonzosamente paralelo**: contar ocurrencias en la primera mitad
del archivo no depende en absoluto de la segunda mitad. Es el caso de uso canónico
de **map-reduce**, y es lo que LogHunter explota.

## 1.2. Qué quiero armar

Quiero armar una aplicación **cliente-servidor** donde:

**El cliente** es una herramienta de línea de comandos. Recibe por argumentos la
ruta del log a analizar y qué consulta quiere resolver, se conecta por TCP al
servidor, envía el pedido, muestra el progreso del análisis mientras ocurre, y al
terminar imprime el informe por pantalla.

```bash
python -m loghunter.cliente --file s3://logs/access.log --query top-ips --top 10 \
                            -i 127.0.0.1 -p 8888
```

El esquema de la URI (`s3://` o `local://`) es lo que determina por dónde leen los
workers; el resto del sistema no cambia.

**El servidor** (al que llamo *coordinador*) no procesa el log. Su trabajo es
**repartir y recomponer**:

1. Acepta conexiones de **varios clientes a la vez**, cada uno pidiendo un
   análisis distinto sobre un archivo distinto.
2. Para cada pedido le pregunta al almacenamiento **cuánto mide** el archivo (un
   `HEAD` si está en MinIO, un `stat` si es local) y calcula los **cortes** por
   aritmética: una lista de pares `(offset_inicio, offset_fin)` que lo dividen en
   tramos del tamaño configurado. **No lee el contenido**: la alineación a fin de
   línea la resuelve cada worker sobre su propio tramo (ver §1.3).
3. Encola **una tarea por tramo** en una cola de tareas distribuida (Celery sobre
   Redis). Lo que viaja por la cola no es el contenido del archivo: es la terna
   `(uri, offset_inicio, offset_fin)` —unos cien bytes— más el nombre de la
   consulta y el `job_id` del pedido.
4. Mientras los workers trabajan, el coordinador **no se bloquea**: sigue
   atendiendo a los demás clientes y les informa el progreso a todos.
5. Cuando todos los tramos volvieron, hace el **reduce**: fusiona los resultados
   parciales en el informe final, lo devuelve al cliente y lo manda a persistir.

**Los workers** son procesos Celery que corren **en máquinas independientes**. Cada
uno toma una tarea y le pide al almacenamiento **solamente su tramo** del archivo
—con un `GET Range` si está en MinIO, o con `mmap` si corre en la misma máquina que
el archivo—, lo alinea a fin de línea, parsea sus líneas y devuelve un contador
parcial (un diccionario `clave → cantidad`). Son intercambiables y sin estado: se
pueden agregar o quitar en caliente, y si uno muere su tarea se reintenta en otro.

**El indexador** es un proceso hijo del coordinador, dedicado a persistir cada
informe en SQLite. Existe separado porque escribir en disco es una operación
bloqueante y de latencia impredecible que no debe demorar la respuesta al cliente.

**El generador** es una herramienta auxiliar que fabrica logs sintéticos de tamaño
controlado —en disco local o subiéndolos al bucket— para poder medir el rendimiento
de forma reproducible y para provocar casos borde a voluntad (líneas corruptas, por
ejemplo).

## 1.3. Dónde está cada cosa

Esta sección es el corazón de la propuesta: cada mecanismo de la materia está
puesto porque el problema lo pide, no para cumplir con la consigna.

### Concurrencia — en el coordinador, con `asyncio`

El coordinador atiende N clientes simultáneos con **un solo hilo y un solo
proceso**. Puede hacerlo porque su trabajo es casi todo espera de I/O: espera que
el cliente mande el pedido, espera que los workers devuelvan los parciales, espera
que el socket se vacíe para escribir la respuesta.

Usar `asyncio` en lugar de un hilo o un proceso por cliente evita el costo de
crearlos, el problema del GIL para el caso I/O-bound, y **elimina las *data races***
sobre las estructuras internas del coordinador: el diccionario de jobs activos lo
toca un solo hilo, en puntos de cesión explícitos marcados con `await`, así que
nadie puede interrumpir un `+= 1` a mitad de camino.

**Lo que `asyncio` no elimina, y hay que decirlo con precisión:** las carreras
*lógicas* que **cruzan** un `await`. Si una corrutina lee un valor, hace `await` y
después escribe en función de lo que leyó, otra corrutina pudo correr en el medio.
El patrón a evitar es *leer → `await` → decidir*; la regla es no ceder el control
dentro de una sección crítica, y cuando la sección necesita esperar algo, protegerla
con un `asyncio.Lock`. El tope de análisis simultáneos usa por eso un
`asyncio.Semaphore` en lugar de un contador chequeado a mano. El detalle está en
`02-arquitectura.md` §2.5.

### Paralelismo — en los workers, con Celery

El parseo de líneas con expresiones regulares es **CPU-bound**. Ahí `asyncio` no
sirve de nada: no hay esperas que aprovechar, hay cálculo. Y los hilos tampoco,
por el GIL. Lo que hace falta son **procesos separados**, y preferentemente en
varias máquinas.

Celery da exactamente eso, y además tres cosas que un `multiprocessing.Pool` local
no da:

- **Distribución real**: los workers viven en otras máquinas; se suman lanzando un
  proceso más contra el mismo Redis, sin tocar el coordinador. Para que eso sea
  cierto de punta a punta, el log también tiene que ser alcanzable desde cualquier
  máquina: de eso se ocupa el backend S3 descrito más abajo.
- **Desacople temporal**: si no hay workers vivos, las tareas quedan encoladas en
  Redis y se procesan cuando alguno aparece. El coordinador no se cae.
- **Reintentos y tolerancia a fallos**: si un worker muere a mitad de un tramo, la
  tarea se vuelve a entregar a otro.

### Asincronismo entre entidades

- **Coordinador ↔ workers**: completamente asincrónico y desacoplado por la cola.
  El coordinador despacha y se olvida; recoge los resultados cuando están.
- **Coordinador ↔ indexador**: asincrónico mediante una `multiprocessing.Queue`.
  El coordinador deposita el informe y sigue; el indexador lo escribe cuando puede.
- **Operador ↔ coordinador**: asincrónico mediante **señales POSIX**. `SIGUSR1`
  provoca el volcado del estado de los jobs activos; `SIGTERM`/`SIGINT` inician un
  apagado ordenado que revoca las tareas en vuelo.

### Acceso al archivo: coordenadas en vez de datos, con dos backends

Esta es la decisión de diseño central del proyecto. Los workers necesitan leer un
tramo de un archivo de 2 GB. Hay dos caminos:

1. **Enviar los datos**: leer cada tramo y mandar los bytes por la cola. Significa
   empujar 2 GB a través de Redis, serializados y en memoria. Absurdo.
2. **Enviar las coordenadas**: mandar `(uri, inicio, fin)` —cien bytes— y que cada
   worker se traiga su rango por su cuenta.

LogHunter hace lo segundo. Lo que queda por definir es **cómo** un worker lee un
rango de un archivo que puede no estar en su máquina, y eso se resuelve con el
módulo `lector.py`: una interfaz mínima (`tamano()` y `leer(inicio, fin)`) con dos
implementaciones que se eligen por el esquema de la URI.

**Backend `local://` — `mmap`.** El kernel mapea el archivo en el espacio de
direcciones del proceso: no hay copia a espacio de usuario, las páginas se traen por
demanda, y **si dos procesos mapean el mismo archivo comparten las mismas páginas
físicas de la caché del sistema**. Es memoria compartida respaldada por archivo, el
mecanismo de IPC más eficiente posible para este caso, y es el camino que se usa
cuando todo corre en una sola máquina.

**Backend `s3://` — `GET` con `Range`.** El log vive en un servicio de
almacenamiento con API S3 (**MinIO**, libre, una imagen de Docker) y el worker pide
exactamente su tramo con una cabecera `Range: bytes=inicio-fin`. Viaja una copia de
32 MB por la red, pero el worker **puede estar en cualquier máquina**.

**Por qué hacen falta los dos.** El backend local es más eficiente, pero ata los
workers al host donde está el log —que en producción es el servidor web, con nginx
y todos sus servicios ya corriendo—. Agregarle M workers ahí no escala: haría falta
una sola máquina enorme en lugar de varias chicas. El backend S3 rompe ese
acoplamiento: el log tiene su propio servicio y cualquier máquina puede leer su
tramo. Como los dos están detrás de la misma interfaz, el worker no sabe cuál está
usando y el proyecto puede demostrar las dos cosas.

**Por qué S3/MinIO y no NFS.** NFS no requiere cambiar código —se monta y `mmap`
sigue funcionando— pero exige administrar un servidor NFS, la coherencia de caché de
`mmap` sobre NFS es frágil, y no aporta nada conceptualmente. Los `Range` requests
de la API S3 son **literalmente** el diseño de coordenadas de este proyecto
expresado sobre HTTP.

### El corte alineado a fin de línea, resuelto en el worker

Cortar cada 32 MB a ciegas partiría un registro al medio. El coordinador no puede
arreglarlo recorriendo el archivo, porque con el backend remoto eso significaría
bajarse los 2 GB solo para buscar saltos de línea. Entonces cada worker alinea su
propio tramo con dos reglas que no requieren hablar con nadie:

1. Si `inicio > 0`, **descarta todo hasta el primer `\n` inclusive**: esa línea
   pertenece al tramo anterior.
2. Lee hasta `fin` y, si la última línea quedó cortada, **pide una cola de 64 KB más
   allá de `fin`** hasta el primer `\n`, para completarla.

Cada línea termina contada por exactamente un worker: los tramos son disjuntos y
cubren el archivo completo. Es el mismo algoritmo del `TextInputFormat` de Hadoop.

### Correlación de pedidos simultáneos

Con tres clientes analizando a la vez, por Redis circulan cientos de tareas y
conteos parciales. El coordinador no tiene que adivinar de quién es cada uno:

- Cada pedido recibe un **`job_id`** (`uuid4`) que se guarda en el diccionario de
  jobs activos junto al `writer` del cliente.
- Las tareas de un pedido se encolan como un **`group` de Celery**, que devuelve un
  **`GroupResult`**: ese objeto agrupa esos parciales y ningún otro.
- El progreso que se le informa al cliente es `GroupResult.completed_count()`, y el
  análisis está terminado cuando llega al número de cortes.
- Si un job no se completa dentro de `--job-timeout`, se revocan sus tareas
  pendientes y se le informa el error **a ese cliente**, sin afectar a los demás.

## 1.4. Consultas que resuelve

| Consulta | Qué devuelve |
|---|---|
| `top-ips` | Las N direcciones IP con más peticiones |
| `status` | Distribución de códigos de respuesta HTTP |
| `urls` | Las N rutas más solicitadas |
| `traffic` | Bytes transferidos agrupados por hora, para localizar picos |

El formato de entrada es el *combined log format* de Apache/Nginx:

```
192.168.1.45 - - [20/Oct/2026:14:32:11 -0300] "GET /api/users HTTP/1.1" 200 1534 "-" "curl/8.1"
```

Las líneas que no matchean el patrón se cuentan como inválidas y se informan, sin
abortar el análisis.

## 1.5. Alcance

### Incluido

- Cliente CLI con las cuatro consultas y salida formateada.
- Coordinador asyncio multi-cliente, con corte de tramos y reduce.
- Workers Celery sobre Redis, ejecutables en máquinas independientes.
- Módulo `lector.py` con los dos backends (`local://` con `mmap` y `s3://` con
  `Range` requests contra MinIO) detrás de una interfaz común.
- Indexador con persistencia en SQLite e histórico de informes consultables.
- Generador de logs sintéticos con tamaño, cantidad de IPs y porcentaje de
  corrupción configurables.
- Tope configurable de análisis pesados concurrentes.
- Apagado ordenado por señales, con revocación de tareas en vuelo.
- Despliegue con `docker-compose`, incluyendo el servicio MinIO.
- Medición de *speedup* reproducible con 1, 2 y 4 workers.
- Timeout por análisis con revocación de las tareas pendientes de ese job.

### Deliberadamente excluido

- Autenticación y autorización de clientes.
- Interfaz web o gráfica (el cliente es CLI).
- Seguimiento de logs en vivo (`tail -f`).
- Formatos de log distintos del *combined* (el parser queda aislado para poder
  sumarlos después).
- Logs comprimidos (`.gz`), porque rompen el supuesto de poder cortar por offsets.
- Replicación o alta disponibilidad del almacenamiento: MinIO se despliega como una
  instancia única.

Las exclusiones están registradas como posibles mejoras en `TODO.md`.

## 1.6. Cómo se demuestra que funciona

- **Que escala**: el mismo archivo analizado con 1, 2 y 4 workers, mostrando la
  reducción del tiempo. Es la evidencia de que el paralelismo es real.
- **Que escala horizontalmente**: workers corriendo fuera de la máquina del log,
  leyendo su tramo desde MinIO, sin que el coordinador cambie ni se reinicie.
- **Que es concurrente**: dos clientes pidiendo análisis distintos a la vez, ambos
  recibiendo progreso, con un solo proceso coordinador.
- **Que está desacoplado**: apagar todos los workers, lanzar un análisis (las
  tareas se acumulan en Redis), y volver a levantar un worker para ver que el
  análisis se completa solo.
- **Que es robusto**: un log generado con `--corrupt-pct 5`, donde el informe
  reporta las líneas inválidas en lugar de fallar.

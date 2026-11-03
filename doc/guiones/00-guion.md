# Guion de presentación — LogHunter

**Si vas a leer una sola parte, leé la primera: "El proyecto explicado en mis
palabras".** Está escrita como lo contás hablando, y cubre las tres cosas que cuesta
tener claras: qué hace el sistema, **en qué momento aparece cada tecnología**, y cuál
fue el cambio que pidió el profesor.

Después viene la **sección 0**, que es el guion de la reunión de aprobación: qué
mostrar y las tres preguntas, con la respuesta redactada para decir en voz alta. El
resto es material de respaldo: el glosario de los conceptos con ejemplos (sección 5)
y la justificación de cada tecnología frente a las que descarté (sección 6).

---

## El proyecto explicado en mis palabras

Esta sección está escrita como lo cuento hablando, no como documentación. Es para
leerla y que me quede la idea completa en la cabeza; lo formal está en las secciones
numeradas.

---

### Qué es el proyecto

Básicamente es **un sistema de análisis de logs**. Un log es el archivo donde un
servidor web va anotando todo lo que le pasa: una línea por cada visita, con la IP
del que entró, qué página pidió, qué le respondió el servidor (200 si salió bien,
404 si no existía, 500 si el servidor explotó) y cuántos bytes le mandó. Esos
archivos crecen sin parar: un servidor con tráfico normal te deja un `access.log` de
2 GB con 18 millones de líneas en unas semanas.

El problema es que si yo quiero sacar información de ahí, tengo que leer el archivo
entero. Y un programa normal lo lee de arriba a abajo, de a una línea, con un solo
núcleo del procesador, y tarda un par de minutos mientras los otros siete núcleos no
hacen nada.

Entonces lo que hace mi sistema es repartir ese trabajo. Y acá está el detalle
importante: **el archivo no se parte de verdad, no lo corto en pedacitos ni genero
archivos nuevos.** El archivo queda intacto donde está. Lo que hago es **calcular de
qué byte a qué byte le toca a cada worker** y mandarle eso. O sea, le mando un
papelito que dice *"andá al archivo tal, leé del byte 0 al byte 33 millones y contame
qué encontrás"*, y a otro *"vos leé del 33 millones al 67 millones"*. Cada uno va,
lee **solo su pedazo**, cuenta lo suyo, y me devuelve su resultado parcial. Después
yo sumo todos los parciales y armo el informe final.

**Y para qué sirve:** para contestar preguntas sobre el log sin esperar dos minutos.
Por ejemplo:

- cuántos errores 500 tiró el servidor, y cuántos 404
- qué IPs son las que más entraron (para detectar a alguien raspando el sitio)
- qué URLs son las más pedidas
- a qué hora del día se me generó el pico de tráfico

Son las cuatro consultas que tengo definidas, pero la idea es que **agregar una
consulta nueva es escribir una función, no cambiar la arquitectura**: la máquinaria
de repartir y juntar es siempre la misma, lo único que cambia es qué cuenta cada
worker cuando mira su pedazo.

> **Ojo con una cosa que digo mal cuando hablo rápido:** los logs **no están en una
> base de datos**. Son archivos, y viven en un almacenamiento aparte (MinIO). La base
> de datos que tengo (SQLite) es solo para guardar el **histórico de los análisis que
> ya corrí** —qué pregunté, cuánto tardó, con cuántos workers—, no los logs. Son dos
> cosas distintas y conviene no mezclarlas.

---

### Dónde aparece cada tecnología

Esto es lo que más me costaba tener claro: no *qué* uso, sino **en qué momento
aparece cada cosa**. Lo cuento siguiendo el camino de un pedido, de punta a punta.

**1. Yo escribo el pedido en una consola.** Eso es `cliente.py`, y para leer las
opciones que le paso (`--file`, `--query`, `--top`) uso **`argparse`**, que es la
librería de Python para argumentos de línea de comandos. Antes de conectarse valida
que la consulta exista y que el puerto sea válido, así no molesto al servidor con un
pedido mal hecho.

**2. El cliente se conecta al servidor por red.** Ahí entran los **sockets TCP**. Un
socket es el extremo de un "cable" virtual entre dos programas; TCP es la variante
confiable, la que garantiza que llegue todo y en orden. Uso TCP y no UDP porque el
informe no puede llegar a la mitad.

**3. Lo que viaja por ese cable es texto JSON**, una línea por mensaje, usando la
librería **`json`**. O sea que el protocolo es mío, hecho a medida, pero mínimo: el
pedido es una línea, cada mensaje de progreso es una línea, y el informe final es
otra línea. El salto de línea es el separador, así no tengo que inventarme nada más
complicado.

**4. El servidor atiende a varios clientes a la vez con un solo hilo.** Eso es
**`asyncio`**, concretamente `asyncio.start_server`. Puede atender a muchos a la vez
sin crear un hilo por cada uno porque su trabajo es casi todo *esperar*: espera el
pedido, espera a los workers, espera que el socket se vacíe. Cada vez que empieza a
esperar algo (eso es un `await`), suelta y atiende a otro. **Acá está la
concurrencia del proyecto.**

**5. El servidor calcula los pedazos.** Y no lee el archivo para eso: le pregunta al
almacenamiento **cuánto mide** —un `HEAD` si está en MinIO, un `os.stat` si es
local— y divide. 2 GB entre 32 MB me dan 67 pedazos. Es una división, no una
lectura.

**6. Deja un pedido de trabajo por cada pedazo.** Acá entran **Celery** y **Redis**,
que son dos cosas distintas y conviene no confundirlas:

- **Celery** es la librería de Python con la que defino la tarea y la despacho. La
  tarea se llama `map_chunk` y recibe `(archivo, desde, hasta, consulta)`. Yo no
  ejecuto la tarea: la **encolo**, con `map_chunk.delay(...)`, y eso vuelve al
  instante.
- **Redis** es el servicio donde esa cola vive físicamente. Es un programa aparte que
  corre en su propio puerto (6379) y guarda en memoria. Ahí quedan las tareas
  pendientes esperando a que alguien las agarre, y ahí también dejan los workers sus
  resultados cuando terminan.

**7. ¿Qué es un worker y quién lo crea?** Un worker es simplemente **un proceso de
Python corriendo aparte**, que no hace nada más que estar conectado a Redis
preguntando "¿hay trabajo?". Lo creo yo a mano, con un comando:
`celery -A loghunter.tasks worker -c 4`. Lo importante es que **el servidor no los
crea ni sabe cuántos hay**: él deja las tareas en Redis y chau. Si hay 2 workers,
tarda el doble que con 4; si no hay ninguno, las tareas se quedan ahí esperando y se
procesan cuando levanto uno. Para tener más paralelismo abro otra terminal y lanzo
otro worker, sin tocar ni reiniciar el servidor. **Acá está el paralelismo del
proyecto**, y son procesos separados y no hilos porque en Python existe el GIL, que
no deja que dos hilos hagan cuentas al mismo tiempo.

**8. El worker lee su pedazo.** Para eso tengo un módulo propio, **`lector.py`**, que
esconde de dónde viene el archivo. Expone dos funciones —"¿cuánto mide?" y
"dame del byte X al byte Y"— y por debajo tiene dos implementaciones:

- si el archivo está en el almacenamiento remoto, usa **`boto3`** (el cliente de la
  API S3) y pide su tramo con un `GET` y una cabecera `Range: bytes=0-33554431`.
  **HTTP ya sabe mandarte un pedazo de un archivo**, no tuve que inventar nada;
- si el archivo está en la misma máquina que el worker, usa **`mmap`**, que es una
  función del sistema operativo que te deja leer un archivo gigante como si fuera un
  array, sin cargarlo en memoria: el kernel te va trayendo solo las páginas que
  tocás.

El worker no sabe cuál de las dos está usando, y eso es lo que me permite tener el
camino rápido en una máquina y el camino escalable en varias.

**9. El worker cuenta.** Parsea cada línea con **`re`** (expresiones regulares) para
sacarle la IP, la URL, el código y los bytes, y va acumulando en un
**`collections.Counter`**, que es básicamente un diccionario que suma solo. Lo que
devuelve es ese diccionario: `{"10.0.0.1": 300, "10.0.0.2": 120}`. Las líneas que
están corruptas las cuenta aparte y sigue, no se cae.

**10. ¿Cómo sé de quién es cada resultado?** Esta era la pregunta que no supe
contestar. Cada pedido que me entra recibe un **`job_id`** (un identificador único,
con `uuid4`), y las 67 tareas de ese pedido no las encolo sueltas: las encolo juntas
como un **`group` de Celery**, que me devuelve un objeto **`GroupResult`**. Ese
objeto es el que agrupa esos 67 resultados y ningún otro. Entonces si tengo tres
clientes a la vez, tengo tres `GroupResult` distintos y **no hay nada que adivinar**:
no es que caen todos los parciales en una bolsa común.

**11. ¿Cómo sé que ya están todos?** Porque sé que son 67 y los cuento:
`GroupResult.completed_count()` me dice cuántos terminaron. Ese mismo número es el
que le mando al cliente como progreso (*"12 de 67"*). Cuando llega a 67, hago la
suma final.

**12. ¿Y si una tarea no termina nunca?** Dos cosas distintas:

- **Si el worker se muere a mitad del pedazo** (lo mataron, se quedó sin memoria),
  Celery se da cuenta y **le vuelve a dar esa misma tarea a otro worker**. Es seguro
  porque la tarea es *idempotente*: contar el mismo pedazo dos veces da el mismo
  resultado, así que el total no se altera.
- **Si el pedido entero se cuelga**, tengo un `--job-timeout` (5 minutos por
  defecto). Cuando vence, cancelo las tareas que quedaban pendientes **de ese
  pedido**, le aviso el error **a ese cliente**, y los otros clientes ni se enteran.

**13. El servidor suma todo y responde.** Junta los 67 diccionarios en uno (si un
worker vio una IP 300 veces y otro la vio 250, el total es 550), lo recorta al top
que pedí, y se lo manda al cliente por el mismo socket.

**14. Y de paso guarda el informe.** Pero acá hay un problema: escribir en disco
**bloquea**, y si lo hago dentro del servidor, mientras espera al disco **se congela
la atención de todos los clientes**. Así que lo saco del servidor: tengo un proceso
hijo aparte, `indexador.py`, creado con **`multiprocessing.Process`**, que es el
único que escribe en la base. La comunicación entre los dos procesos es:

- una **`multiprocessing.Queue`**, donde el servidor deja el informe terminado y
  sigue de largo sin esperar;
- un **`multiprocessing.Value` con un `Lock`**, que son contadores compartidos
  (análisis activos, informes guardados). El `Lock` no es opcional: `contador += 1`
  son en realidad tres pasos (leer, sumar, escribir) y si dos procesos lo hacen a la
  vez se pierde un incremento.

El indexador escribe con **`sqlite3`**, en dos tablas: una fila por análisis y una
fila por cada resultado. Guardo también cuánto tardó y con cuántos workers, que es
lo que me permite armar la tabla de tiempos sin cronometrar a mano.

**15. Para apagar el sistema o espiar qué está haciendo, uso señales.** La librería
**`signal`**. Si le mando `kill -USR1`, el servidor imprime en pantalla los análisis
que tiene en curso con su progreso; si le mando `SIGTERM`, deja de aceptar clientes,
cancela las tareas en vuelo, espera a que el indexador termine de escribir y recién
ahí se cierra. Es comunicación entre procesos sin tener que abrir un puerto de
administración.

**16. Y para levantar todo junto, Docker.** Un `docker-compose.yml` que arranca
cuatro servicios: **Redis** (la cola), **MinIO** (el almacenamiento del log), el
coordinador y los workers. Subir el paralelismo es `--scale worker=4`, sin tocar una
línea de código.

#### Resumido en una tabla

| Momento | Con qué |
|---|---|
| Leer las opciones de la consola | `argparse` |
| Hablar entre cliente y servidor | sockets **TCP** |
| El formato de los mensajes | **`json`**, una línea por mensaje |
| Atender a muchos clientes a la vez | **`asyncio`** → *concurrencia* |
| Saber cuánto mide el archivo | `HEAD` (S3) o `os.stat` (local) |
| Definir y despachar el trabajo | **Celery** |
| Guardar la cola y los resultados parciales | **Redis** |
| Hacer las cuentas en paralelo | **procesos worker de Celery** → *paralelismo* |
| Leer solo mi pedazo del archivo | **`lector.py`**: `boto3` + `Range`, o `mmap` |
| Dónde vive el log | **MinIO** (API S3) |
| Parsear y contar | **`re`** + **`collections.Counter`** |
| Saber de qué pedido es cada parcial | `job_id` + **`GroupResult`** de Celery |
| Recuperarse de un worker caído | reintento de Celery + tarea idempotente |
| Cortar un pedido colgado | `--job-timeout` + `revoke()` |
| Guardar sin frenar al servidor | **`multiprocessing`**: `Process`, `Queue`, `Value` + `Lock` |
| Guardar el histórico | **`sqlite3`** |
| Apagar ordenado / ver el estado | **`signal`** |
| Levantar todo | **Docker** / `docker-compose` |

---

### El cambio que me pidió el profesor

#### Cómo estaba antes

En la primera versión, el archivo de log era **un archivo en un disco**, en una ruta
tipo `/data/access.log`. El servidor lo abría con `mmap` para buscar dónde cortarlo,
y cada worker lo abría con `mmap` también para leer su pedazo.

Eso funcionaba y era rapidísimo, porque `mmap` no copia nada. **Pero tenía un
problema escondido:** `mmap` necesita que el archivo esté **en la misma máquina** que
el proceso que lo lee. O sea que mis workers **no podían salir de la máquina donde
estaba el log**.

Y la máquina donde está el log es el servidor web. Ahí ya está corriendo nginx con
todos sus servicios. Si yo quería más paralelismo, tenía que meterle más workers **a
esa misma máquina**, hasta quedarme sin CPU. Como me lo dijo el profesor: así no
escalás, **necesitás una máquina gigante en lugar de varias chicas**.

En el documento yo lo tenía declarado como "costo asumido: hace falta un volumen
compartido", pero el profesor lo marcó como la limitación que valía la pena sacar, y
tenía razón: es justo lo que le quita sentido a tener una cola distribuida.

#### Qué cambié

Saqué el archivo del disco local y lo puse en **su propio servicio de
almacenamiento**: **MinIO**, que es una implementación libre de la API de S3 (la
misma de Amazon) y se levanta con una imagen de Docker.

Ahora el worker **no abre un archivo: le pide un pedazo por red**, con un `GET` y una
cabecera `Range: bytes=67108864-100663296`. Y acá está lo que más me gustó del
cambio: **mandar coordenadas en vez de datos es exactamente lo que HTTP ya sabe
hacer** con los `Range` requests. No tuve que inventar un protocolo para repartir
pedazos de archivo: usé el estándar que ya existía para eso.

Para no perder el camino rápido, armé **`lector.py` con dos backends** detrás de la
misma interfaz: `local://` sigue usando `mmap` (y es el modo en que voy a hacer la
demo en una sola máquina), y `s3://` usa los `Range` requests. El worker no sabe cuál
está usando, así que no dupliqué nada.

El cambio arrastró **una consecuencia que me gusta**: el servidor ya no puede
recorrer el archivo para alinear los cortes al fin de cada línea, porque bajarse los
2 GB para buscar saltos de línea sería absurdo. Entonces la alineación pasó al
worker, con dos reglas que no necesitan que nadie se coordine con nadie:

1. si mi pedazo no arranca en el byte 0, **descarto todo hasta el primer salto de
   línea** (esa línea cortada es del pedazo anterior);
2. leo hasta mi fin y, si mi última línea quedó cortada, **pido un poquito más allá**
   hasta el próximo salto de línea para completarla.

Así cada línea del archivo la cuenta **exactamente un** worker. Es el mismo algoritmo
que usa Hadoop. Y de paso ahora es literalmente cierto que el coordinador **no lee el
log**: solo pregunta cuánto mide.

#### Qué gané

| | Antes | Ahora |
|---|---|---|
| Dónde vive el log | Un archivo en el disco del servidor web | En MinIO, su propio servicio, en su propia máquina |
| Cómo lee el worker | `mmap` de una ruta local | `GET` + `Range: bytes=ini-fin` por HTTP (o `mmap`, si es local) |
| Dónde pueden correr los workers | **Solo en la máquina del log** | **En cualquier máquina** que alcance Redis y MinIO |
| Cómo sumo capacidad | Más workers en la misma máquina, hasta quedarme sin CPU | Sumo máquinas; cada una levanta sus workers |
| Quién alinea las líneas | El coordinador, recorriendo el archivo | Cada worker, sobre su propio pedazo |
| Qué lee el coordinador | El archivo entero, para buscar los saltos de línea | Nada. Solo pregunta el tamaño |
| El cuello de botella | El servidor web, que además tenía que correr los workers | Ninguno de los tres: cada pieza escala por separado |

**La frase para explicar el cambio:** *antes los workers tenían que vivir donde
vivía el archivo, y el archivo vive en el servidor web, así que escalar significaba
agrandar el servidor web. Ahora el archivo tiene su propio servicio y los workers le
piden su pedazo por HTTP, así que puedo sumar máquinas en vez de agrandar una.*

---

## 0. La reunión de aprobación de la arquitectura

> **Estado: ya presentado y aprobado en principio.** El profesor dio el visto bueno
> a la arquitectura —*"está bien como lo organizaste, tenés concurrencia, trabajo con
> workers y networking, en principio cumple los requisitos"*— con **un cambio
> pedido** y tres detalles para definir. Todo está aplicado en los documentos y
> listado en [`02-arquitectura.md` §2.10](02-arquitectura.md). El resumen:
>
> | Qué observó | Qué se hizo |
> |---|---|
> | Los workers quedaban atados a la máquina del log (el servidor web), así que no escalaba horizontalmente | Se agregó `lector.py` con dos backends: `local://` con `mmap` y `s3://` con `GET Range` contra **MinIO**. Los workers ya corren en máquinas independientes |
> | No estaba definido cómo correlacionar los parciales con cada pedido ni cómo saber que llegaron todos | `job_id` por pedido + un `GroupResult` de Celery por job, con `completed_count()` y `--job-timeout` |
> | Un `await` dentro de una sección crítica sí puede dar problemas de concurrencia | Se corrigió la afirmación de que asyncio "elimina de raíz" las carreras: elimina las *data races*, no las que cruzan un `await` |
> | Preguntó por el protocolo de capa de aplicación | Ya estaba decidido: JSON delimitado por `\n` sobre TCP. Ahora está entre las respuestas preparadas |
>
> Lo que sigue abajo es el guion de esa instancia. Se mantiene porque sirve igual
> para la defensa final.

**La instancia.** Todavía no hay código: le mostrás el diagrama, le contás qué vas a
usar, justificás las elecciones, él te da algún tip y te aprueba. Son tres preguntas
y están todas respondidas acá abajo, en castellano simple y en el orden en que las
va a hacer.

**Qué le muestro:** el diagrama de [`02-arquitectura.md`](02-arquitectura.md),
abierto en GitHub (Mermaid se renderiza solo). Empezá por el **diagrama simple** de
§2.1, que tiene al lado el guion de seis pasos para explicarlo señalando cada
número; el diagrama completo está más abajo en la misma sección, por si pide
detalle. Con ese archivo en pantalla alcanza.

---

### Pregunta 1 — "¿Qué vas a hacer?"

Esto es lo que decís en voz alta. Son cuatro ideas, en este orden.

> **1. El problema.** Los servidores web guardan todo lo que pasa en un archivo de
> texto llamado log: una línea por cada visita, con la IP del que entró, qué pidió,
> qué le respondió el servidor y cuántos bytes mandó. Esos archivos se vuelven
> gigantes: 2 gigas, 18 millones de líneas en pocas semanas.
>
> **2. La pregunta que quiero responder.** Si quiero saber cosas útiles de ahí
> —*qué IPs me están generando más tráfico*, *cuántos errores estoy devolviendo*,
> *qué páginas son las más visitadas*, *a qué hora tuve el pico*— tengo que leer el
> archivo entero. Un programa normal lo lee de arriba a abajo, de a una línea, y
> tarda dos minutos y medio usando **un solo núcleo** del procesador. Los otros
> siete no hacen nada.
>
> **3. La idea.** Contar las IPs de la primera mitad del archivo **no necesita
> saber nada** de la segunda mitad. Entonces puedo partir el archivo en pedazos y
> que varios procesos cuenten **al mismo tiempo**, cada uno en su pedazo. Después
> junto lo que contó cada uno y tengo el total. Eso baja el tiempo de 148 segundos
> a 41 con cuatro procesos.
>
> **4. Qué programo.** Tres cosas. Un **cliente** de consola donde escribo qué
> análisis quiero. Un **servidor** que recibe el pedido, calcula en qué pedazos
> cortar el archivo y los reparte. Y varios **workers**, que son los que realmente
> cuentan, y puedo tener 1 o 10 según cuánta velocidad necesite, en máquinas
> distintas, porque el log vive en un almacenamiento aparte al que todos le piden
> solo su pedazo.
>
> **5. La respuesta, toda junta.** Si te pide que lo resumas, o si lo querés decir
> de entrada y después desarrollar:
>
> Es **un sistema cliente-servidor de análisis de logs** que toma **el archivo de
> log de un servidor web, de varios gigabytes**, y lo procesa **de la siguiente
> forma: lo parte en pedazos de 32 MB, reparte los pedazos entre varios procesos
> que cuentan en paralelo, posiblemente en varias máquinas, y después junta los
> resultados parciales en uno solo**.
> Eso permite responder preguntas como **qué IPs generan más tráfico, cuántos
> errores 500 devolví, qué URLs son las más pedidas o a qué hora fue el pico de
> tráfico**, en **mucho menos tiempo que un programa que lee el archivo de arriba a
> abajo** —41 segundos en vez de 148— porque el trabajo se hace en paralelo y
> además el servidor puede atender a varios clientes a la vez.

**La estructura de esa frase**, por si la querés rearmar con tus palabras:

> Es un sistema de **[qué tipo de sistema]** que toma **[qué datos de entrada]**,
> los procesa **[cómo]**, y permite responder **[qué preguntas]** en menos tiempo
> gracias a **[por qué es más rápido]**.

Con el punto 5 ya contestaste la pregunta. Los puntos 1 a 4 son para desarrollarla
si te deja hablar, y lo que sigue es para cuando él repregunte.

#### Si te pregunta cómo se llama ese esquema

Se llama **map-reduce**. Son dos palabras y significan exactamente lo que ya
dijiste:

- **map** = partir el trabajo en pedazos y que cada uno se haga por separado.
- **reduce** = juntar los pedacitos de resultado en uno solo.

**El ejemplo para explicarlo:** el recuento de votos de una elección. Cada mesa
cuenta **solo sus votos** y no necesita saber nada de las otras mesas, así que las
20.000 mesas pueden contar al mismo tiempo (*map*). Después se suman los telegramas
de todas las mesas y tenés el total del país (*reduce*).

En el proyecto, con dos pedazos:

```
worker 1 cuenta el pedazo 1  →  {"10.0.0.1": 300, "10.0.0.2": 120}
worker 2 cuenta el pedazo 2  →  {"10.0.0.1": 250, "10.0.0.5":  90}
                                ────── los junto ──────
total                        →  {"10.0.0.1": 550, "10.0.0.2": 120, "10.0.0.5": 90}
```

**Lo único que hace falta para que funcione:** que los pedazos sean independientes.
Contar IPs lo es. Si la pregunta fuera *"cuánto duró la sesión de cada usuario"*, no
serviría, porque las líneas de una misma sesión podrían caer en pedazos distintos.
Eso decilo vos antes de que lo pregunte él: muestra que entendés el límite.

#### Las dos palabras de la materia: dónde está cada una

Esto es lo que él está evaluando, así que conviene tenerlo clarísimo. **En el
proyecto hay dos cosas distintas pasando a la vez, y usan técnicas distintas.**

| | Qué pasa ahí | Cómo lo resuelvo | Por qué |
|---|---|---|---|
| **Paralelismo** | Los workers cuentan los pedazos del archivo | Varios **procesos** separados | Contar es **trabajo**: cuatro procesos cuentan cuatro veces más rápido |
| **Concurrencia** | El servidor atiende a varios clientes a la vez | **Un solo** hilo, con `asyncio` | Atender es casi todo **esperar**: no hace falta más de uno |

**El ejemplo que lo fija: un restaurante con un mozo y cuatro cocineros.**

El mozo atiende 20 mesas él solo y nadie se queja, porque tomar un pedido lleva 30
segundos y después solo espera que la cocina lo haga. Va de mesa en mesa, nunca está
quieto. Poner cuatro mozos no serviría de nada: no saldrían más platos. **Eso es
concurrencia.**

Los cocineros son otra historia: cocinar no es esperar, es trabajo. Cuatro cocineros
hacen cuatro platos al mismo tiempo, y con uno solo tardarías cuatro veces más por
rápido que vaya el mozo. **Eso es paralelismo.**

> **En una línea: el servidor es el mozo, los workers son los cocineros. El mozo no
> cocina y los cocineros no atienden mesas.**

**Y por qué uso los dos y no uno:** porque cada parte se traba por un motivo
distinto. Si pongo un proceso por cliente, gasto un proceso entero en algo que solo
espera. Si pongo un solo hilo para todo, los clientes se atienden bien pero las
cuentas las hace uno solo y no gano velocidad. Usando cada técnica donde
corresponde, **148 segundos bajan a 41**. Eso no es una opinión, lo mido y lo
muestro.

---

### Pregunta 2 — "¿Qué vas a usar?"

Agrupado por para qué sirve cada cosa, que es más fácil de contar que una lista
suelta.

**Para que el cliente y el servidor se hablen por red**

| | Qué hace |
|---|---|
| **Sockets TCP** | El "cable" por donde se mandan el pedido y el informe. TCP garantiza que llegue todo y en orden |
| **asyncio** | Lo que permite que **un solo** proceso atienda a muchos clientes a la vez sin trabarse |

**Para hacer las cuentas en paralelo**

| | Qué hace |
|---|---|
| **Celery** | Una "lista de tareas" compartida: el servidor deja ahí los pedazos a contar y los workers los van agarrando |
| **Redis** | Es dónde vive físicamente esa lista de tareas, y también dónde los workers dejan sus resultados |

**Para leer un archivo de 2 GB sin morir, desde cualquier máquina**

| | Qué hace |
|---|---|
| **MinIO** (API S3) | El servicio donde vive el log. Le puedo pedir "dame del byte X al byte Y" por HTTP, así el worker no necesita estar en la máquina del archivo |
| **`mmap`** | Lo mismo pero cuando el archivo sí está en la máquina del worker: deja leer un pedazo sin cargar el archivo entero en memoria |
| **`lector.py`** | Un módulo propio que esconde cuál de los dos se está usando. El worker pide "del byte X al Y" y no sabe si vino de un disco o de la red |

**Para lo demás**

| | Qué hace |
|---|---|
| **`multiprocessing`** (`Queue`, `Value`, `Lock`) | Cómo se pasan datos entre dos procesos que no comparten memoria |
| **`signal`** | Apagar el servidor ordenadamente, o pedirle que me diga qué está haciendo, desde la terminal |
| **`sqlite3`** | Guardar un historial de los análisis que se corrieron |
| **`re`** (expresiones regulares) | Sacarle la IP, la URL y el código a cada línea del log |
| **`argparse`** | Los argumentos de la línea de comandos |
| **Docker** | Levantar Redis, el servidor y los workers con un solo comando |

**Si te pregunta por los requisitos de la materia**, los cinco están cubiertos:
sockets concurrentes (asyncio), IPC (`mmap`, `Queue`, `Value`+`Lock`, señales),
asincronismo de I/O (asyncio), cola distribuida (Celery + Redis) y argumentos CLI
(`argparse`). Cada uno está desarrollado al final de esta sección, en
**"Los requisitos del final, uno por uno"**.

---

### Pregunta 3 — "¿Y por qué eso y no otra cosa?"

Una línea por cada una. No hace falta más.

| Elegí | En vez de | Y si pregunta por qué, decís |
|---|---|---|
| **TCP** | UDP | "Porque el informe tiene que llegar completo y en orden. Con UDP podría llegar la mitad y el cliente ni se enteraría" |
| **asyncio** | Un hilo o un proceso por cliente | "Porque el servidor casi no calcula, solo espera. Abrir un proceso entero para algo que espera es tirar recursos" |
| **Celery** | `multiprocessing.Pool` | "Porque el Pool solo funciona en mi máquina y si un worker se muere perdés el trabajo. Con Celery puedo sumar workers en otra máquina y si uno se cae la tarea se la da a otro" |
| **Redis** | RabbitMQ | "Porque Redis me sirve para las dos cosas que necesito —la lista de tareas y los resultados— y se levanta con un comando" |
| **Mandar la ubicación** del pedazo | Mandar el pedazo entero | "Porque mandar los datos serían 2 GB viajando. Mando 'archivo tal, del byte X al byte Y': son 100 bytes y el worker lo abre él mismo" |
| **`mmap`** | `open()` + `read()` | "Porque `read()` copia el archivo a memoria. `mmap` no copia: le va pidiendo al sistema solo los pedazos que necesita" |
| **MinIO / API S3** | Un volumen compartido o NFS | "Porque con un volumen compartido los workers tienen que correr en la misma máquina que el log, que es el servidor web, y ahí no escalás. Con S3 el log tiene su propio servicio y cualquier máquina le pide su pedazo" |
| **Los dos backends** | Elegir uno solo | "Porque `mmap` es imbatible en una sola máquina y S3 es el que me deja escalar a varias. Están detrás de la misma interfaz, así que el worker no sabe cuál usa" |
| **Un proceso aparte para la base** | Guardar desde el servidor | "Porque guardar en disco traba el programa mientras espera. Si lo hago en el servidor, se congela la atención de **todos** los clientes" |
| **SQLite** | PostgreSQL | "Porque guardo una fila por análisis, no por línea de log. Es poquísimo y no vale la pena instalar un motor de base de datos" |

#### Las mismas ocho, explicadas con lo que pasa en MI proyecto

La tabla de arriba es para contestar rápido. Esto es para cuando repregunte. Cada
una tiene la misma forma: **qué necesito yo acá** y **qué me pasaría si eligiera la
otra opción**.

##### 1. ¿Por qué TCP y no UDP?

**Qué pasa en mi proyecto:** el cliente manda un pedido de una línea
(`{"file": "s3://logs/access.log", "query": "top-ips", "top": 10}`) y recibe de vuelta
mensajes de progreso (*12 de 67 tramos*) y al final una tabla con 10 IPs y sus
conteos. Son varios kilobytes que la red parte en pedazos y manda por separado.

**Qué necesito:** que lleguen **todos** los pedazos, **en orden**, y que el cliente
se entere si el servidor se murió a mitad del análisis.

**Qué pasaría con UDP:** el informe se partiría en, digamos, tres datagramas, y
podrían llegar el 1 y el 3 nada más. El cliente imprimiría una tabla con 6 IPs en
vez de 10 **y no daría ningún error**: creería que ese es el informe completo. O
podrían llegar desordenados y la IP con más tráfico aparecería tercera. Y como UDP
no tiene conexión, si el servidor se cae a los 20 segundos el cliente se queda
esperando para siempre sin enterarse de nada.

**Cuándo sí usaría UDP:** si en vez de un informe estuviera mandando una métrica por
segundo —"el worker 3 está al 80% de CPU"— perder una muestra no cambiaría nada.

---

##### 2. ¿Por qué asyncio y no un hilo o un proceso por cliente?

**Qué pasa en mi proyecto:** mirá lo que hace el servidor para atender UN cliente:

| Paso | Qué es | Cuánto tarda |
|---|---|---|
| Leer el pedido del socket | **esperar** | lo que tarde el cliente |
| Calcular los 67 cortes del archivo | trabajar | ~0,2 s |
| Encolar las 67 tareas en Redis | **esperar** | ms |
| Que los workers terminen | **esperar** | ~41 s |
| Sumar los 67 diccionarios parciales | trabajar | ms |
| Escribir el informe en el socket | **esperar** | ms |

De 41 segundos, el servidor **trabaja menos de medio segundo**. El resto es estar
parado esperando. Esa es toda la justificación.

**Qué pasaría con un hilo por cliente:** 10 clientes = 10 hilos, de los cuales 9
están dormidos esperando a Redis. No ganás velocidad (el GIL no deja que dos hilos
calculen a la vez, y además no hay nada que calcular). Y aparece un problema nuevo:
el contador de *jobs activos* pasa a ser tocado por 10 hilos a la vez, así que
`contador += 1` se puede corromper y necesito locks **dentro del servidor**. Con
asyncio eso no puede pasar: hay un solo hilo y solo cede el control en los `await`,
que los escribo yo y los veo.

**Qué pasaría con un proceso por cliente:** cada proceso es un intérprete de Python
entero (unos 10-15 MB) más su propia conexión a Redis, creado para quedarse dormido
41 segundos. Con 50 clientes son 50 procesos y cientos de megas de RAM durmiendo. Es
lo que hice en el TP2 y ahí estaba bien, porque cada cliente hacía **cálculo** de
imágenes; acá el servidor no calcula nada.

**Cómo queda con asyncio:** un solo proceso, un solo hilo, y 50 clientes son 50
corrutinas que pesan unos kilobytes cada una.

**El matiz honesto, por si lo pregunta:** el `.get()` con el que recojo los
resultados de Celery **sí es bloqueante**, no tiene `await`. Ese lo mando a un hilo
aparte con `asyncio.to_thread` para no congelar el loop. Es un hilo para esperar, no
un hilo por cliente.

---

##### 3. ¿Por qué Celery y no `multiprocessing.Pool`?

**Qué pasa en mi proyecto:** cada análisis genera 67 tareas. Yo quiero poder correr
el mismo análisis con 1, 2 y 4 workers para mostrar que el tiempo baja (es la demo
principal). Y un worker puede morirse de verdad: se queda sin memoria, o lo mato yo
con `kill` para mostrar que el sistema aguanta.

**Qué pasaría con un Pool:** los 4 procesos del Pool son hijos de mi servidor y
viven en mi máquina. Tres problemas concretos:

1. **Si quiero sumar la notebook de al lado, no puedo.** El Pool no cruza la
   frontera de la máquina. Celery sí: la otra máquina corre `celery worker`
   apuntando al mismo Redis y ya está sumando capacidad.
2. **Si un worker muere, pierdo el análisis entero.** El `map` del Pool tira una
   excepción y se cayeron los 2 GB, no un tramo de 32 MB. Con Celery, Redis le
   vuelve a entregar **ese tramo** a otro worker y el total sale igual, porque
   contar el mismo tramo dos veces da el mismo resultado (es idempotente).
3. **Para pasar de 4 a 8 workers tendría que reiniciar el servidor**, porque el
   tamaño del Pool se fija al crearlo. Con Celery abro otra terminal, corro
   `celery -A loghunter.tasks worker -c 4` otra vez, y el servidor ni se enteró.

**Y hay una demo que solo es posible con Celery:** apago **todos** los workers,
lanzo un análisis, las 67 tareas se quedan guardadas en Redis esperando, levanto un
worker y el análisis se completa solo. Con un Pool eso no existe: si no hay
capacidad, el que encola se queda bloqueado.

---

##### 4. ¿Por qué Redis y no RabbitMQ?

**Qué pasa en mi proyecto:** necesito **dos cosas distintas**, no una:

- un lugar donde **dejar** las 67 tareas para que los workers las agarren (eso se
  llama *broker*);
- un lugar donde los workers **dejen** los 67 diccionarios parciales para que yo los
  recoja (eso se llama *backend de resultados*).

**Qué me da Redis:** las dos con un solo servicio. `docker run -d -p 6379:6379
redis` y funciona; en el `docker-compose.yml` son tres líneas.

**Qué pasaría con RabbitMQ:** es un mejor broker —tiene ruteo, confirmaciones,
prioridades— pero **no sirve como backend de resultados**. Así que necesitaría
RabbitMQ *más* otra cosa para guardar los parciales: dos servicios, dos
configuraciones, dos cosas que explicar en el `INSTALL.md`. Para 67 tareas de 100
bytes cada una, no gano absolutamente nada.

**Lo honesto si insiste:** si tuviera miles de tareas por segundo y no pudiera
perder ninguna ante un corte de luz, RabbitMQ sería la elección correcta. Para este
volumen, no.

---

##### 5. ¿Por qué mando la ubicación del pedazo y no el pedazo?

**Qué pasa en mi proyecto:** 2 GB dividido en tramos de 32 MB = **67 tareas**.

**Qué pasaría si mandara los bytes:** 67 tareas × 32 MB = **2 GB entrando a Redis**
y 2 GB saliendo. Y Redis guarda todo **en memoria RAM**, así que necesitaría 2 GB de
RAM libres nada más que para la cola de tareas. Más el tiempo de serializar y
deserializar: cada worker tendría que desempaquetar 32 MB antes de empezar a contar.

**Qué mando en realidad:** `("s3://logs/access.log", 0, 33554432, "top-ips")`. Unos
100 bytes. Las 67 tareas juntas son menos de 7 KB. El worker va a buscar su rango él
mismo.

**Y acá está lo elegante:** el worker pide su tramo con un `GET` y una cabecera
`Range: bytes=0-33554431`. O sea que **mandar coordenadas en vez de datos es
exactamente lo que hace HTTP** cuando pedís un rango de un archivo. No inventé nada:
usé el mecanismo estándar que ya existe para esto.

---

##### 5b. ¿Por qué MinIO (API S3) y no un volumen compartido o NFS?

**Qué pasa en mi proyecto:** el log pesa 2 GB y lo tienen que leer M workers que
quiero poder repartir entre varias máquinas.

**Qué pasaría con un volumen compartido:** `mmap` necesita que el archivo esté en una
ruta local, así que **los workers quedan atados a la máquina donde está el log**. Y
esa máquina es el servidor web: ahí ya corre nginx con todos sus servicios. Si quiero
más paralelismo tengo que meterle más workers **a esa misma máquina**, hasta que me
quede sin CPU. No escalo con varias máquinas chicas, necesito una gigante.

**Qué pasaría con NFS:** funciona y no me obliga a cambiar una línea de código —monto
el NFS en `/data` y `mmap` sigue andando—. Pero tengo que administrar un servidor
NFS, la coherencia de caché de `mmap` sobre NFS es frágil, y conceptualmente no
aporta nada: sigue siendo "finjamos que es un disco local".

**Qué hago en realidad:** el log vive en **MinIO**, que es una implementación libre
de la API de S3, y el worker pide su tramo con `GET` + `Range: bytes=ini-fin`. Tres
cosas a favor:

1. **Los workers pueden estar en cualquier máquina.** Solo necesitan alcanzar la URL
   de Redis y la de MinIO. Eso es escalado horizontal real.
2. **Es un estándar**, no un invento mío: la misma API que usa Amazon S3. El código
   no depende de MinIO, depende del protocolo.
3. **Los `Range` requests son mi diseño expresado sobre HTTP.** Mandar
   `(archivo, desde, hasta)` y que el otro lado te devuelva solo ese pedazo es
   literalmente lo que ya venía haciendo, ahora por red.

**El precio:** el tramo viaja por la red (32 MB por tarea) en vez de mapearse sin
copia. Lo asumo, porque lo que compro es poder sumar máquinas.

---

##### 6. ¿Por qué `mmap` y no `open()` + `read()`?

**Qué pasa en mi proyecto:** el worker 3 necesita los bytes **67.108.864 a
100.663.296** del archivo. Nada más. Ni el principio ni el final.

**Qué pasaría con `read()`:** tengo dos opciones y las dos son peores.

- Leer el archivo entero: 2 GB dentro de la memoria del worker, por 4 workers = 8 GB.
  No entra.
- `seek()` al byte 67.108.864 y `read(32 MB)`: funciona, pero **copia** esos 32 MB
  del kernel a la memoria de mi proceso. Con 4 workers trabajando son 128 MB de
  copias que no hacían falta.

**Qué hace `mmap`:** le pide al kernel una "ventana" al archivo. Lo leo como si
fuera un array gigante y el sistema me trae **solo las páginas que toco**, por
demanda. No hay copia. Y como los 4 workers mapean el mismo archivo, **comparten las
mismas páginas en la caché del sistema**: el archivo no se duplica en memoria cuatro
veces.

**Dónde quedó `mmap` después del cambio a MinIO:** es el backend `local://` del
lector, el camino rápido para cuando todo corre en una sola máquina —que es el modo
en que voy a hacer la demo—. Sigue siendo el mecanismo de **IPC por memoria
compartida** del proyecto: dos workers del mismo host que mapean el mismo archivo
comparten las páginas, así que el log no se duplica en memoria por worker.

**Y el servidor ya no lo usa**, porque ya no lee el archivo: solo pregunta cuánto
mide.

---

##### 7. ¿Por qué un proceso aparte para la base y no guardar desde el servidor?

**Qué pasa en mi proyecto:** al terminar un análisis guardo una fila en la tabla
`informes` y 10 filas en `resultados`, todo en una transacción. Un `INSERT` con su
`commit` puede tardar 5 milisegundos... o 300 si el disco está ocupado con otra cosa.

**Qué pasaría si lo hiciera desde el servidor:** `sqlite3` es una biblioteca
**bloqueante** — no existe `await insert`. Durante esos 300 ms el event loop está
**detenido**: los otros 5 clientes conectados no reciben ni un byte de progreso,
porque el único hilo que tengo está esperando al disco. Es exactamente lo que
asyncio no te deja hacer, y está anotado como el invariante número 1 del proyecto.

**Cómo funciona como está:** el servidor hace `queue.put(informe)` —microsegundos—
le responde al cliente y sigue atendiendo. El indexador, que es otro proceso, saca
de esa cola y escribe cuando puede. Si el disco tarda un segundo, al cliente no le
importa: ya tiene su informe en pantalla.

**Si te pregunta por qué un proceso y no un hilo:** un hilo también funcionaría,
porque `sqlite3` libera el GIL mientras espera al disco. Elegí un proceso por dos
razones: si SQLite se cuelga o explota, no se lleva al servidor con él; y la
comunicación entre esos dos procesos (`Queue` para el informe, `Value` + `Lock` para
los contadores) es justamente el **IPC** que pide la materia.

---

##### 8. ¿Por qué SQLite y no PostgreSQL?

**Qué pasa en mi proyecto:** guardo **una fila por análisis**, no una por línea de
log. Si corro 50 análisis a lo largo del desarrollo, son 50 filas en `informes` y
unas 500 en `resultados`. (Si guardara una fila por línea de log serían 18 millones
y la respuesta sería otra.)

**Y hay un solo escritor:** el indexador. Nadie más toca la base. El problema
clásico de SQLite —varios procesos escribiendo a la vez y peleándose— **no me
afecta**, porque por diseño hay uno solo.

**Qué pasaría con PostgreSQL:** habría que instalar un servidor de base de datos,
crear un usuario y una base, agregar un servicio más al `docker-compose` y escribir
todo eso en el `INSTALL.md`... para guardar 50 filas. Es complejidad que no compra
nada.

**Cuándo cambiaría de opinión:** si guardara cada línea parseada del log, o si
hubiera varios coordinadores escribiendo la misma base al mismo tiempo.

---

#### Si te quedás en blanco

Esta frase contesta casi cualquier "¿por qué?" de este proyecto:

> **Porque esta parte del sistema *espera* y esta otra *calcula*.** Donde se espera
> uso asyncio, que atiende muchas cosas con un solo hilo. Donde se calcula uso
> procesos separados, porque Python no puede acelerar cálculo con hilos.

Si de toda esta página te acordás de una sola cosa, que sea esta. Justifica asyncio,
Celery, los workers como procesos y el proceso aparte de la base.

#### Las cinco palabras que tenés que poder decir sin dudar

Si no entendés estas cinco, no importa lo bien que esté el diagrama. Están
explicadas largo y con ejemplos en la sección 5, pero la versión corta es:

| Palabra | En una línea |
|---|---|
| **Socket** | El extremo de un cable virtual entre dos programas por red |
| **Concurrencia** | Varias tareas **en progreso** a la vez, alternándose. Para cuando se espera |
| **Paralelismo** | Varias tareas **ejecutándose** literalmente a la vez, en núcleos distintos. Para cuando se calcula |
| **GIL** | El candado de Python que impide que dos hilos calculen a la vez. Por eso uso procesos y no hilos |
| **IPC** | Cómo se pasan datos dos procesos, que por defecto no ven la memoria del otro |

**Lo que conviene no decir:** *"porque me gusta"* o *"porque lo vimos en clase"*. No
te lo va a rechazar, pero la única pregunta real de esta instancia es si entendés
**por qué** cada pieza está donde está. Siempre hay una razón de una línea en las
tablas de arriba.

---

### Si te da tips o te pide cambios

Es lo esperable y es la intención declarada en `FINAL.md`: recortar si es muy
extenso, agregar si es muy simple. Qué hacer:

1. **Anotalo en el momento**, aunque sea en el celular.
2. **Volcalo al `doc/` y commiteá**, con un mensaje tipo
   `doc: ajustes de arquitectura acordados con el profesor`. El enunciado pide
   explícitamente que los cambios de requisitos se commiteen.
3. Si te dice que **es muy simple y quiere más**: tenés la lista de exclusiones en
   [`01-descripcion.md` §1.5](01-descripcion.md) — formatos de log adicionales,
   logs comprimidos, interfaz web, seguimiento en vivo, autenticación. Elegís una
   de ahí en vez de improvisar.
4. Si te dice que **es demasiado**: lo primero que sacaría es Docker y el histórico
   en SQLite, que son extras opcionales, no requisitos.

### Lo que NO hace falta en esta instancia

- Código. Nada. Todavía no se empieza.
- Tener decidido el detalle fino (tamaño de pedazo, nombres de los archivos, formato
  exacto del protocolo). Puede cambiar durante el desarrollo y es normal.
- Memorizar el glosario de la sección 5. Está acá para que vos entiendas lo que
  estás diciendo, no para recitarlo.

### Después de que te lo apruebe

Empezás a codear y le vas mostrando avances periódicamente, con commits seguidos
—aunque no funcione todavía. Una semana antes de la mesa tiene que estar completo y
visado, más los `INSTALL.md`, `README.md`, `INFO.md` y `TODO.md`.

---

### Los requisitos del final, uno por uno

`FINAL.md` pide cinco cosas obligatorias en el código. Esta tabla es el mapa: qué
pide, qué significa en castellano, con qué lo cumplo y qué hace exactamente esa
tecnología en MI proyecto. Si te pregunta *"¿y dónde está tal requisito?"*, la
respuesta está acá.

#### Los cinco obligatorios

**1. "Uso de Sockets con conexión de clientes múltiples de manera concurrente"**

| | |
|---|---|
| **Qué significa** | Que haya un programa servidor escuchando en un puerto y varios clientes puedan conectarse **al mismo tiempo**, sin que uno tenga que esperar a que el otro termine |
| **Con qué lo cumplo** | Sockets **TCP** levantados con `asyncio.start_server`, en el puerto 8888 |
| **Qué hace en mi proyecto** | `coordinador.py` abre el puerto y queda escuchando (`asyncio.start_server` sobre un socket TCP). Cada vez que un `cliente.py` se conecta (`socket` + `asyncio.open_connection` del lado del cliente), se crea una corrutina que lo atiende (`async def atender_cliente`). Si se conectan 5 clientes, hay 5 corrutinas vivas a la vez en un solo proceso y un solo hilo (el *event loop* de `asyncio`), y ninguna frena a las otras. El pedido y el informe van como una línea de texto JSON (`json` + `reader.readline()`) |
| **Cómo lo demuestro** | Demo 2: dos clientes pidiendo análisis distintos a la vez, los dos viendo su barra de progreso avanzar, con un solo proceso servidor corriendo |

**2. "Uso de mecanismos de IPC"**

| | |
|---|---|
| **Qué significa** | IPC = *Inter-Process Communication*. Dos procesos distintos **no comparten memoria**: lo que uno guarda en una variable, el otro no lo ve. Si tienen que pasarse datos, hay que usar un mecanismo explícito del sistema operativo |
| **Con qué lo cumplo** | Cuatro mecanismos distintos, cada uno donde corresponde |
| **`mmap`** | Entre los workers de un mismo host, **a través del archivo**: cada uno mapea solo su tramo (`mmap.mmap` con `offset` y `length`), vía el backend `local://` de `lector.py`. Es memoria compartida respaldada por archivo: los procesos que mapean el mismo archivo comparten las páginas de la caché del kernel, así que el log no se duplica en memoria por worker |
| **`multiprocessing.Queue`** | Del servidor al indexador. El servidor deja el informe terminado en la cola y sigue (`queue.put(informe)`, no bloquea); el indexador lo saca y lo escribe en la base (`queue.get()` en bucle bloqueante, en su propio proceso creado con `multiprocessing.Process`) |
| **`multiprocessing.Value` + `Lock`** | Contadores que ven los dos procesos: análisis activos, informes guardados, líneas procesadas (`Value('i', 0)` en memoria compartida). El `Lock` es obligatorio porque `contador += 1` son tres pasos y se puede corromper (`with lock:` alrededor de cada incremento) |
| **Señales POSIX** | Del shell al servidor. `kill -USR1 <pid>` le pide que muestre qué está haciendo (`signal.SIGUSR1` + handler registrado con `loop.add_signal_handler`); `SIGTERM`/`SIGINT` arrancan un apagado ordenado que revoca las tareas en vuelo (`signal` + `AsyncResult.revoke()` de Celery) y espera al indexador (`proceso.join()`) |
| **API S3 (MinIO)** | Entre los workers y el almacenamiento, cuando están en máquinas distintas: `GET` con `Range: bytes=ini-fin` (`boto3`). Redis transporta las *coordenadas*; esto transporta los *datos*, sin copiar los 2 GB |
| **Cómo lo demuestro** | Demo 5: `kill -USR1 <pid>` y el servidor imprime los análisis en curso con su progreso |

**3. "Uso de asincronismo de I/O"**

| | |
|---|---|
| **Qué significa** | *I/O* es todo lo que implica esperar algo de afuera: la red, el disco. **Asincronismo de I/O** es no quedarse congelado durante esa espera: mientras una tarea espera, el programa hace otra cosa |
| **Con qué lo cumplo** | `asyncio`: el *event loop* y las corrutinas (`async def` / `await`) |
| **Qué hace en mi proyecto** | Cada `await` del servidor es un punto donde dice *"esto va a tardar, usá el tiempo en otra cosa"*: esperar el pedido (`await reader.readline()`), esperar que el socket se vacíe para escribir (`await writer.drain()`), esperar los parciales de los workers (`await asyncio.to_thread(...)`). Mientras el cliente A espera 41 segundos a sus workers, el loop (`asyncio.get_event_loop`) atiende al cliente B, le manda progreso al C y acepta al D. Un solo hilo que nunca se queda quieto. Y el tope de análisis pesados simultáneos lo pone un `asyncio.Semaphore(--max-jobs)` |
| **El detalle fino** | Lo que **sí** bloquea lo saco del loop: el `.get()` de Celery va a un hilo aparte (`asyncio.to_thread`) y el `INSERT` de disco va a otro proceso (`multiprocessing.Queue` → `indexador.py`). Es el invariante nº 1 del proyecto: nada bloqueante dentro del *event loop* |

**4. "Uso de cola de tareas distribuidas"**

| | |
|---|---|
| **Qué significa** | Una lista de trabajos pendientes que vive **afuera** de mi programa, en un servicio aparte. El que produce trabajo lo deja ahí y se desentiende; los que ejecutan lo van agarrando, y pueden estar en otra máquina |
| **Con qué lo cumplo** | **Celery** (la librería que define y despacha las tareas) sobre **Redis** (el servicio donde la cola vive físicamente) |
| **Qué hace en mi proyecto** | El servidor parte el log en 67 tramos (`mmap` para hallar los cortes) y deja 67 tareas en la cola (`celery.group(map_chunk.s(ruta, ini, fin, query))` + `apply_async()`, con Redis como *broker*). Los workers las van sacando (`celery -A loghunter.tasks worker`), cuentan su tramo (`mmap` + `re` + `collections.Counter`) y devuelven el parcial por el mismo Redis (*backend* de resultados). El servidor los recoge (`AsyncResult.get()` dentro de `asyncio.to_thread`) y hace el *reduce* sumando los `Counter`. El servidor **no sabe cuántos workers hay ni en qué máquina están** |
| **Por qué es "distribuida" de verdad** | Puedo levantar un worker en otra computadora apuntando al mismo Redis (`--broker redis://ip-del-coordinador:6379/0`) y al mismo MinIO (`--s3-endpoint`), y empieza a trabajar sin tocar una línea de código del servidor. **Nada lo ata a la máquina del log**, que era la limitación que el profesor pidió sacar. Y si un worker muere, Redis reentrega el tramo a otro (reintento de Celery, seguro porque `map_chunk` es idempotente) |
| **Cómo lo demuestro** | Demo 1: el mismo archivo con 1, 2 y 4 workers → `148 s → 79 s → 41 s`. Demo 3: sin ningún worker prendido las tareas se acumulan y se procesan cuando levanto uno |

**5. "Parseo de argumentos por línea de comandos"**

| | |
|---|---|
| **Qué significa** | Que los programas se configuren con opciones al invocarlos, no con valores escritos a mano en el código |
| **Con qué lo cumplo** | `argparse`, en los cuatro ejecutables |
| **Qué hace en mi proyecto** | `cliente.py`: `--file`, `--query`, `--top`, `-i`, `-p` (`argparse.ArgumentParser`). `coordinador.py`: `-i`, `-p`, `--chunk-mb`, `--max-jobs`, `--broker`, `--db` (`argparse`). `generar.py`: `--out`, `--size-mb`, `--ips`, `--corrupt-pct`, `--seed` (`argparse`). Los workers se configuran con la CLI propia de Celery (`celery worker -c 4`). La validación la hace `argparse` con `type=` y `choices=` (que la consulta exista, que `--top` sea positivo, que el puerto sea válido) antes de abrir el socket |

#### Los opcionales ("otros aspectos que puede considerarse")

| Lo que ofrece `FINAL.md` | ¿Lo hago? | Con qué y para qué |
|---|---|---|
| Despliegue en contenedores Docker | **Sí** | Un `Dockerfile` y un `docker-compose.yml` levantan Redis, **MinIO**, el coordinador y los workers (`docker compose up -d`). Escalar el paralelismo es `--scale worker=4`, sin tocar código. Ningún servicio comparte filesystem con otro: el log vive dentro de MinIO y se alcanza por HTTP |
| Almacenamiento en base de datos | **Sí** | SQLite (`sqlite3`, escrito únicamente por `indexador.py`) con el histórico de análisis: tablas `informes` y `resultados`. Guarda `duracion` y `workers`, que es lo que me permite armar la tabla de *speedup* con datos del propio sistema (`SELECT`) en vez de cronometrar a mano |
| Celery para resolver tareas en paralelo | **Sí** | Es el corazón del proyecto, no un extra (`celery` + `redis` como broker y backend; la tarea es `map_chunk` en `tasks.py`) |
| Entorno visual (web, desktop) | **No** | Deliberadamente excluido: el cliente es CLI, con barra de progreso en terminal (`argparse` + escritura en `stdout`). Está anotado en `01-descripcion.md` §1.5 y queda como mejora futura en el `TODO.md` |

> **Si te dice que es poco**, la lista de exclusiones de §1.5 es de donde sacás qué
> agregar. **Si te dice que es mucho**, los tres primeros opcionales son lo primero
> que se recorta, porque los cinco obligatorios ya están cubiertos sin ellos.

#### Lo que `FINAL.md` pide del repositorio (no del código)

Esto no lo va a preguntar, pero es lo que te habilita a rendir:

| Lo que pide | Estado |
|---|---|
| Directorio `final/` con un `doc/` adentro | ✅ es esto que estás leyendo |
| Descripción verbal de la app | ✅ `01-descripcion.md` |
| Gráfico de la arquitectura (nodos, conectividad, IPC) | ✅ `02-arquitectura.md` |
| Lista de funcionalidades de cada entidad | ✅ `03-entidades.md` |
| Tema discutido y **aprobado antes de codear** | ⏳ es esta reunión |
| Commits periódicos durante el desarrollo | ⏳ después de la aprobación |
| `INSTALL.md`, `README.md`, `INFO.md`, `TODO.md` | ⏳ se escriben con el código |
| Visado del profesor, mínimo una semana antes de la mesa | ⏳ |

---

## 1. El pitch en 30 segundos

> **LogHunter** analiza archivos de log enormes partiéndolos en pedazos y
> procesándolos en paralelo con varios procesos a la vez, posiblemente en varias
> máquinas. Un cliente de consola pide un análisis, un servidor lo reparte, varios
> workers hacen las cuentas y el servidor junta los resultados.

## 2. El problema

Un `access.log` de un servidor web llega a 2 GB y 18 millones de líneas. Querés
saber qué IPs generan más tráfico o cuántos errores 500 devolviste. Con un script
secuencial tarda minutos, usa un solo núcleo y no se puede acelerar.

Pero contar en la primera mitad del archivo **no depende** de la segunda mitad.
Entonces se puede partir y repartir. Eso es lo que hace LogHunter.

## 3. Cómo funciona, en 5 pasos

1. El **cliente** se conecta por red al servidor y le pide: *"analizá
   `s3://logs/access.log` y dame las 10 IPs con más peticiones"*.
2. El **coordinador** (el servidor) no lee el log: le pregunta al almacenamiento
   **cuánto mide** y calcula **dónde cortarlo**, produciendo una lista de pares
   `(byte inicial, byte final)` que lo dividen en tramos de 32 MB.
3. Mete **una tarea por tramo** en una cola. Por la cola no viajan los datos: viaja
   `(uri del archivo, byte 0, byte 33554432)`, unos 100 bytes.
4. Los **workers** sacan tareas de la cola. Cada uno le pide al almacenamiento
   **solo su tramo** (`GET Range`, o `mmap` si corre en la misma máquina), lo alinea
   a fin de línea, cuenta, y devuelve un diccionario `{IP: cantidad}`.
5. El coordinador **suma todos los diccionarios parciales**, arma el informe, se lo
   manda al cliente y lo guarda en una base de datos.

Mientras todo esto pasa, el coordinador sigue atendiendo a otros clientes.

## 4. Las piezas

| Archivo | Qué es | Qué hace |
|---|---|---|
| `cliente.py` | Programa de consola | Pide el análisis y muestra el informe |
| `coordinador.py` | Servidor | Atiende clientes, corta el archivo, reparte, junta |
| `tasks.py` | Tarea de los workers | Cuenta dentro de un tramo |
| `lector.py` | Módulo | Trae un rango de bytes del log, sin que al worker le importe de dónde |
| `indexador.py` | Proceso hijo | Guarda los informes en la base |
| `generar.py` | Herramienta | Fabrica logs de prueba del tamaño que quiera |
| Redis | Servicio | La cola donde se ponen las tareas |
| MinIO | Servicio | Donde vive el log; sirve rangos de bytes por HTTP |
| SQLite | Archivo de base | Histórico de análisis |

---

## 5. Glosario: los conceptos, en castellano

Cada concepto tiene un ejemplo para fijarlo, y después dónde aparece en el
proyecto.

### Socket
Un **socket** es el extremo de un canal de comunicación por red. Dos tipos:

- **TCP (STREAM)**: confiable. Hay handshake de conexión, acuse de recibo (ACK), y
  los datos llegan completos y en orden. Lo usa HTTP, SSH.
- **UDP (DGRAM)**: no confiable. Mandás un datagrama y si llega, llega. Más rápido,
  sin conexión. Lo usa DNS para consultas.

**Ejemplo.** TCP es una llamada telefónica: marcás, el otro atiende, sabés si se
cortó, y las palabras llegan en el orden en que las dijiste. UDP es gritar por la
ventana: es más rápido, pero no sabés si el otro escuchó ni te enterás si no.

Si mando el informe por UDP en tres datagramas, podría llegar el 1, el 3, y el 2
nunca. El cliente imprimiría un informe incompleto creyendo que está completo.

*En LogHunter:* TCP, porque el informe no puede llegar incompleto ni desordenado.

### Concurrencia vs. paralelismo
No son lo mismo y es **la** distinción de la materia.

- **Concurrencia**: varias tareas *en progreso* a la vez, alternándose en el mismo
  núcleo. Sirve cuando las tareas pasan el tiempo **esperando** (red, disco).
- **Paralelismo**: varias tareas *ejecutándose* literalmente al mismo tiempo, en
  núcleos distintos. Sirve cuando las tareas **calculan**.

**Ejemplo.** Un restaurante con **un mozo** y **cuatro cocineros**.

El mozo es **concurrente**: atiende 20 mesas él solo, porque tomar un pedido lleva
30 segundos y después solo espera. Va de mesa en mesa, nunca está quieto, y nadie
siente que lo desatienden. Poner cuatro mozos no serviría de nada: no se sirven más
platos, porque el cuello de botella no está ahí.

Los cocineros son **paralelos**: cocinar no es esperar, es trabajo. Cuatro
cocineros hacen cuatro platos al mismo tiempo. Y acá sí, un cocinero solo
multiplicaría el tiempo por cuatro, por más rápido que vaya el mozo.

**La ventaja de usar los dos es que cada parte tiene un cuello de botella
distinto.** Si usás un solo modelo para todo, pagás de más en un lado y te queda
corto en el otro.

#### En LogHunter: el ejemplo concreto

Dos clientes conectados, un archivo de 2 GB que se parte en 67 tramos. Cuatro
combinaciones posibles:

| Diseño | Qué pasa | Tiempo |
|---|---|---|
| Solo concurrencia (asyncio para todo, 1 núcleo) | El coordinador atiende bien a los 2 clientes, pero los 67 tramos los cuenta él, de a uno | **148 s** |
| Solo paralelismo (un proceso por cliente, sin asyncio) | Se crea un proceso entero por cliente... que se pasa el tiempo esperando. Con 100 clientes, 100 procesos durmiendo | lento y caro |
| Ninguno (script secuencial) | Un cliente por vez, un tramo por vez | 148 s **por cliente, en fila** |
| **Los dos (el diseño elegido)** | 1 hilo atiende a los 2 clientes mientras 4 workers cuentan los 67 tramos en paralelo | **41 s** |

Dicho en una línea: **el coordinador es el mozo, los workers son los cocineros.**
El mozo no cocina y los cocineros no atienden mesas.

### asyncio, event loop y corrutinas
`asyncio` permite atender muchas conexiones con **un solo proceso y un solo hilo**.

El **event loop** es un bucle que tiene una lista de tareas. Una **corrutina** es
una función declarada `async def` que, cuando llega a un `await`, **cede
voluntariamente el control** al loop para que atienda otra cosa mientras ella
espera. Cuando lo que esperaba está listo, el loop la retoma donde quedó.

La analogía de la cátedra: un ajedrecista experto juega contra 24 novatos a la vez.
No juega 24 partidas en paralelo — hace su movida y pasa al tablero siguiente
mientras los demás piensan.

**Ejemplo.** Cada `await` es un punto donde la función dice *"esto va a tardar,
usá el tiempo en otra cosa"*:

```python
async def atender_cliente(reader, writer):
    pedido = await reader.readline()      # ← cede: mientras el cliente escribe,
                                          #   el loop atiende a otro cliente
    parciales = await esperar_workers()   # ← cede: 41 segundos de espera que el
                                          #   loop usa para atender a los demás
    writer.write(informe)
    await writer.drain()                  # ← cede: espera que se vacíe el socket
```

Línea de tiempo con dos clientes:

```
t=0s    cliente A conecta  → el loop empieza a atenderlo
t=0.1s  A está esperando a los workers → el loop pasa a otra cosa
t=2s    cliente B conecta  → el loop lo atiende (A sigue esperando)
t=41s   vuelven los parciales de A → el loop retoma a A y le responde
t=43s   vuelven los de B → el loop retoma a B
```

Nunca hubo dos cosas ejecutándose al mismo tiempo. Hubo **una sola**, que nunca se
quedó quieta esperando.

**Ventaja extra, con su asterisco:** como hay un solo hilo y las cesiones son
explícitas (`await`), **no hay *data races***: entre dos `await` nada puede
interrumpirte, así que un `+= 1` no se puede corromper.

**Pero sí puede haber carreras que cruzan un `await`**, y conviene decirlo bien
porque es la repregunta fina. Si leés un valor, hacés `await`, y después escribís en
función de lo que leíste, otra corrutina pudo correr en el medio:

```python
# MAL: la invariante se rompe en el await
if len(self.jobs) < self.max_jobs:      # A y B pasan el chequeo
    await self.preparar_job()           # ← cede el control: entra B
    self.jobs[job_id] = job             # terminan 2 jobs con max_jobs=1
```

Las dos soluciones: **no ceder el control dentro de la sección crítica** (leer,
decidir y escribir sin `await` en el medio), o un **`asyncio.Lock`** si la sección
necesita esperar algo. En este proyecto el tope de análisis simultáneos usa por eso
un `asyncio.Semaphore` en vez de chequear un contador a mano.

> **El `Lock` de `multiprocessing` me protege del sistema operativo; el
> `asyncio.Lock` me protege de mis propios `await`.** Son dos problemas distintos.

### GIL (Global Interpreter Lock)
En CPython, un candado global permite que **solo un hilo ejecute bytecode Python a
la vez**. Consecuencia práctica: los **hilos no sirven para acelerar cálculo** en
Python. Para paralelismo real hacen falta **procesos** separados.

**Ejemplo.** Sumar de 1 a mil millones, repartido:

| Cómo | Tiempo | Por qué |
|---|---|---|
| 1 hilo | 40 s | — |
| 4 **hilos** | ~40 s (o peor) | El GIL los obliga a turnarse. Nunca calculan a la vez, y encima se paga el costo de cambiar de hilo |
| 4 **procesos** | ~10 s | Cada proceso tiene su propio intérprete y su propio GIL. Sí corren en paralelo |

**El matiz importante:** el GIL **se libera durante la espera de I/O**. Por eso 4
hilos descargando 4 páginas web sí van más rápido: no están calculando, están
esperando. El GIL molesta para cálculo, no para espera.

*En LogHunter:* por eso los workers son **procesos** (parsean, es cálculo) y el
coordinador puede ser **un solo hilo** (espera, no calcula).

### IPC (Inter-Process Communication)
Cada proceso tiene su propia memoria: no pueden verse las variables.

**Ejemplo del problema.** Si hacés `fork()` y el hijo ejecuta `contador += 1`, el
padre sigue viendo el valor viejo. No hay error, no hay aviso: simplemente son dos
copias independientes. Para que dos procesos compartan algo hay que pedirlo
explícitamente, y eso es IPC.

| Mecanismo | Qué es | Ejemplo |
|---|---|---|
| **`mmap`** | El kernel mapea un archivo dentro de la memoria del proceso. Se lee como si fuera un array, sin copiarlo. Si **dos procesos mapean el mismo archivo, comparten las mismas páginas físicas** | Un libro de 2000 páginas en un atril: para leer la página 500 te paras ahí. `read()` sería fotocopiar el libro entero para leer una página. *El worker 3 lee los bytes 67.108.864 a 100.663.296 y nada más* |
| **`multiprocessing.Queue`** | Una cola entre procesos. Por debajo es un pipe con lock y serialización. Uno mete, otro saca | La bandeja de pedidos de la cocina: el mozo deja el papelito y se va, no espera a que lo cocinen. *El coordinador deja el informe y responde al cliente sin esperar el `INSERT`* |
| **`Value` + `Lock`** | Un número en un segmento de memoria compartida, más un candado para tocarlo | Una pizarra en la pared que todos ven, con una sola fibra: para escribir hay que tener la fibra. *El contador de jobs activos* |
| **Señales** | Un aviso asincrónico al proceso (`SIGTERM`, `SIGUSR1`). No se "manda un dato": se interrumpe al proceso y corre un handler | Golpear la puerta: el que está adentro interrumpe lo que hace y atiende. *`kill -USR1 <pid>` y el coordinador vuelca su estado* |

### Condición de carrera y por qué el Lock
`contador += 1` **no es una operación atómica**: son tres pasos (leer, sumar,
escribir).

**Ejemplo.** Dos procesos incrementan un contador que vale 5. Lo correcto sería
terminar en 7:

```
 proceso A          proceso B          contador
 ─────────────────────────────────────────────────
 lee → 5                                  5
                    lee → 5               5
 suma → 6                                 5
                    suma → 6              5
 escribe 6                                6
                    escribe 6             6   ← ¡debería ser 7!
```

Se perdió un incremento. Nadie dio error. Y el bug es **intermitente**: depende de
dónde caiga el cambio de contexto, así que a veces funciona y a veces no, que es lo
peor que le puede pasar a un programa.

El **Lock** obliga a que entren de a uno: A toma el candado, hace los tres pasos, lo
suelta, y recién entonces entra B. Es el error de `th_errorcontador.py` de la
cátedra.

### Celery, broker, backend y worker
Una **cola de tareas distribuida**. Tres roles:

- **Broker** (Redis): la cola donde se encolan las tareas pendientes.
- **Worker**: proceso que saca una tarea, la ejecuta y devuelve el resultado.
- **Backend** (Redis también): donde queda guardado el resultado hasta que alguien
  lo recoge.

**Ejemplo.** El riel de tickets de una cocina. El mozo pincha el pedido en el riel
(*broker*) y se va; no le importa si hay dos cocineros o seis, ni cuál lo va a
tomar. Cada cocinero libre descuelga un ticket (*worker*), cocina, y deja el plato
en la ventanilla de salida (*backend*). Si no hay ningún cocinero, los tickets se
acumulan en el riel y se cocinan cuando llegue alguno: **nada se pierde**.

En código son dos líneas:

```python
resultado = map_chunk.delay(ruta, 0, 33554432, "top-ips")  # vuelve al instante
# ... el coordinador sigue haciendo otras cosas ...
parcial = resultado.get()   # recoge el resultado cuando lo necesita
```

Lo importante: el que encola **no sabe ni le importa** cuántos workers hay ni en
qué máquina están.

### Map-reduce
Patrón de dos fases:

- **Map**: dividir el trabajo en partes independientes y procesar cada una por
  separado → resultados parciales.
- **Reduce**: fusionar los parciales en el resultado final.

**Ejemplo.** El recuento de una elección. Cada mesa cuenta **solo sus votos**
(*map*) — no necesita saber nada de las otras mesas, y las 20.000 mesas pueden
contar al mismo tiempo. Después se suman los telegramas de todas las mesas
(*reduce*) para tener el total del país.

En LogHunter, con dos tramos:

```
worker 1 (tramo 0-32MB)   → {"10.0.0.1": 300, "10.0.0.2": 120}
worker 2 (tramo 32-64MB)  → {"10.0.0.1": 250, "10.0.0.5":  90}
                             ─────────── reduce ───────────
resultado final           → {"10.0.0.1": 550, "10.0.0.2": 120, "10.0.0.5": 90}
```

**La condición que lo hace posible:** que las partes sean independientes. Contar en
el primer tramo no necesita nada del segundo. Si la consulta fuera *"¿cuánto tardó
cada sesión de usuario?"*, las líneas de una sesión podrían caer en tramos
distintos y este esquema no serviría tal cual.

---

## 6. Decisiones: esto sí, aquello no

| Elegí | Descarté | Por qué |
|---|---|---|
| **TCP** | UDP | El informe tiene que llegar completo y en orden. UDP sirve para telemetría donde perder un dato no importa; acá sí importa |
| **asyncio** en el coordinador | Un hilo por cliente (`threading`) | Su trabajo es casi todo espera de I/O. Hilos agregan costo de creación, no esquivan el GIL y habilitan *data races* que con asyncio no existen, porque las cesiones son explícitas y visibles |
| **asyncio** | Un proceso por cliente (`fork` / `socketserver.ForkingMixIn`) | Un proceso completo por conexión es carísimo para una tarea que solo espera. Lo usé en el TP2 y era justificado ahí porque cada cliente hacía cálculo; acá el coordinador no calcula |
| **Celery** (procesos distribuidos) | `multiprocessing.Pool` local | El Pool paraleliza, pero queda atado a una máquina, no reintenta si un worker muere, y si no hay capacidad el proceso que encola se bloquea. Celery da distribución real, reintentos y desacople temporal |
| **Celery** | `concurrent.futures.ProcessPoolExecutor` | Mismo caso que el Pool: es una interfaz más cómoda, pero igual de local |
| **Redis** como broker | RabbitMQ | Redis cubre broker **y** backend con un solo servicio y se levanta con un `docker run`. RabbitMQ es más robusto pero agrega una pieza y configuración que el proyecto no necesita |
| **Mandar coordenadas** `(uri, inicio, fin)` | Mandar los bytes del tramo por la cola | Mandar los datos significa empujar 2 GB serializados por Redis. Con coordenadas viajan 100 bytes y cada worker trae su rango por su cuenta |
| **MinIO (API S3)** como almacenamiento del log | Volumen compartido / NFS | Con un volumen compartido los workers quedan atados a la máquina del log —el servidor web— y no hay escalado horizontal: haría falta una máquina enorme en lugar de varias chicas. NFS lo evitaría sin tocar código, pero exige administrar un servidor NFS y la coherencia de `mmap` sobre NFS es frágil. La API S3 es un estándar, se levanta con una imagen de Docker, y sus `Range` requests **son** este diseño expresado sobre HTTP |
| **Dos backends detrás de una interfaz** (`lector.py`) | Uno solo | `mmap` es imbatible en una sola máquina y es el mecanismo de IPC por memoria compartida del proyecto; S3 es el que permite escalar a varias. Con los dos detrás de `tamano()` / `leer(inicio, fin)`, el worker no sabe cuál usa |
| **Alinear las líneas en el worker** | Alinearlas en el coordinador | Con el log remoto, que el coordinador busque los `\n` significa bajarse los 2 GB: justo lo que el diseño evita. Cada worker descarta su primera línea parcial y completa la última, y los tramos quedan disjuntos sin coordinación. Es el algoritmo del `TextInputFormat` de Hadoop |
| **`mmap`** (backend local) | `open()` + `read()` normal | `read()` copia del kernel a memoria del proceso. `mmap` no copia: mapea y trae páginas por demanda, y los procesos que mapean el mismo archivo comparten las páginas físicas |
| **Proceso `indexador` aparte** | Escribir en la base desde el event loop | `sqlite3` es bloqueante. Un `INSERT` esperando al disco dentro del loop **congela la atención de todos los clientes**. Aislarlo deja la latencia de disco fuera del camino crítico |
| **`multiprocessing.Queue`** | Un pipe crudo (`os.pipe`) | La Queue ya resuelve serialización y sincronización. Un pipe crudo me obligaría a implementar framing y locks a mano, sin ganar nada |
| **SQLite** | PostgreSQL / MySQL | El volumen es bajo: una fila por análisis, no por línea de log. Y hay un solo escritor (el indexador). No necesita servidor, lo que simplifica la instalación |
| **JSON** en el protocolo | `pickle` | `pickle` ejecuta código al deserializar: aceptar pickles de un cliente cualquiera es un agujero de seguridad. JSON es inspeccionable y no ata el cliente a Python. (En el TP2 usé pickle porque mandaba arrays de imágenes; acá mando un pedido chico y estructurado) |
| **Señales POSIX** para control | Un puerto de administración | Para apagar ordenado o pedir un volcado de estado, una señal alcanza y no agrega superficie de red |

---

## 7. La demo

1. **Que escala de verdad**: mismo archivo con 1, 2 y 4 workers. El tiempo baja.
   `148 s → 79 s → 41 s`. Es la prueba de que el paralelismo existe.
2. **Que es concurrente**: dos clientes pidiendo análisis distintos al mismo tiempo,
   los dos recibiendo progreso, con **un solo** proceso coordinador.
3. **Que está desacoplado**: apago todos los workers, lanzo un análisis (las tareas
   se acumulan en Redis), levanto un worker y el análisis se completa solo.
3b. **Que escala horizontalmente**: un worker corriendo **fuera** de la máquina del
   log, leyendo su tramo desde MinIO con `Range` requests, sin que el coordinador
   cambie ni se reinicie.
4. **Que es robusto**: un log generado con `--corrupt-pct 5`; el informe reporta las
   líneas inválidas en vez de explotar.
5. **Señales**: `kill -USR1 <pid>` y el coordinador vuelca los jobs activos.

---

## 8. Preguntas probables y cómo las contesto

**¿Por qué asyncio y no hilos?**
El coordinador casi no calcula, solo espera. Con hilos pago creación, no esquivo el
GIL y me expongo a condiciones de carrera. Con asyncio, un hilo atiende cientos de
conexiones y el estado interno lo toca uno solo.

**¿Por qué Celery y no un Pool?**
El Pool es local y no tolera fallos. Celery me deja sumar workers en otra máquina
contra el mismo Redis sin tocar el coordinador, reintenta si un worker muere, y si
no hay ninguno las tareas esperan en la cola.

**¿Por qué no mandás el contenido del archivo a los workers?**
Porque serían 2 GB por Redis, que además guarda en memoria. Mando coordenadas y cada
worker va a buscar su rango: con `GET` + `Range: bytes=ini-fin` si el log está en
MinIO, o con `mmap` si corre en la misma máquina que el archivo. Las dos cosas están
detrás de la misma interfaz (`lector.py`), así que el worker no sabe cuál usa.

**¿Qué pasa si una línea queda partida entre dos tramos?**
Lo resuelve cada worker sobre su propio tramo, sin hablar con nadie: si su tramo no
arranca en 0, **descarta todo hasta el primer `\n`** (esa línea es del tramo
anterior), y si su última línea quedó cortada, **lee un poco más allá de su fin**
hasta el próximo `\n` para completarla. Cada línea queda contada por exactamente un
worker. Es el mismo algoritmo del `TextInputFormat` de Hadoop.

Antes esto lo hacía el coordinador recorriendo el archivo, pero con el log en un
almacenamiento remoto eso significaría bajarse los 2 GB solo para buscar saltos de
línea, que es justo lo que el diseño evita.

**¿Qué pasa si un worker se cae a mitad de un tramo?**
La tarea es idempotente (reprocesar el mismo tramo da el mismo resultado), así que
Celery se la reasigna a otro worker sin alterar el total.

**¿Dónde está el riesgo de condición de carrera?**
En dos lugares distintos. **Entre procesos**: los contadores compartidos entre
coordinador e indexador, donde `+= 1` no es atómico y van protegidos con
`multiprocessing.Lock`. **Dentro del coordinador**: no hay *data races* porque es un
solo hilo, pero sí puede haber carreras lógicas si una sección crítica contiene un
`await` —ahí otra corrutina puede entrar y cambiar lo que yo ya leí—. La regla es no
ceder el control dentro de la sección crítica, y usar `asyncio.Lock` cuando la
sección tenga que esperar algo.

**¿Qué protocolo usás entre el cliente y el servidor?**
Uno propio, mínimo: **JSON delimitado por `\n`** sobre TCP. Una línea por mensaje:
el pedido va en una línea, los mensajes de progreso en una línea cada uno, y el
informe final en otra. El delimitador de línea me evita inventar un framing por
longitud, y JSON en vez de `pickle` porque `pickle` ejecuta código al deserializar y
el servidor acepta conexiones de cualquiera.

**¿Cómo sabés de qué cliente es cada conteo parcial?**
Cada pedido tiene un `job_id`, y sus 67 tareas se encolan como un **`group` de
Celery**, que me devuelve un `GroupResult`. Ese objeto agrupa esos 67 parciales y
ningún otro: no hay una bolsa común donde haya que adivinar de quién es cada uno.

**¿Cómo sabés que ya llegaron todos los pedazos?**
Porque sé que son 67 y los cuento: `GroupResult.completed_count()`. Es el mismo
número que le mando al cliente como progreso. Cuando llega a 67 hago el *reduce*. Y
si alguno no vuelve nunca, hay un `--job-timeout` que revoca las tareas pendientes
de ese job y le informa el error a ese cliente, sin afectar a los demás.

**¿Los workers tienen que estar en la misma máquina que el log?**
No, y es a propósito. El log vive en un servicio aparte con API S3 (MinIO) y cada
worker pide su tramo con `GET` + `Range: bytes=ini-fin`. Si estuviera en un volumen
compartido, los workers quedarían atados al servidor web y no podría escalar con
varias máquinas.

**¿Por qué TCP y no UDP?**
Porque el informe tiene que llegar completo y ordenado. UDP sería razonable si
estuviera ingiriendo métricas donde perder una muestra no cambia nada.

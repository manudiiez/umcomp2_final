# Resumen del milestone 2: núcleo de análisis local

Issue #39. Documenta, en un solo lugar, qué se hizo en los issues #3 a #8 y en
qué archivo vivió cada parte. Para los comandos de verificación y sus
resultados reales, ver [VERIFICACION_NUCLEO.md](VERIFICACION_NUCLEO.md).

## Resumen

Esta etapa implementó el **núcleo de análisis local** de LogHunter: parsear
logs combined, calcular las cuatro consultas, generar datos de prueba
reproducibles, leer rangos de bytes de un archivo local y dividir el
trabajo en tramos sin perder ni duplicar registros. Todo corre en un solo
proceso Python, sin Redis, MinIO, Celery ni sockets todavía — eso es trabajo
de los milestones siguientes. Se verificó comparando el resultado calculado
por tramos contra una referencia secuencial (que procesa el archivo entero
sin fragmentar), para distintos tamaños de archivo y de tramo, incluyendo
casos límite.

## Por archivo / directorio

### `src/loghunter/parser.py` (#3)

Interpreta una línea de log combined (formato Apache/Nginx) y la convierte
en un `Registro` con IP, timestamp con huso horario, método, destino,
código HTTP y bytes. Valida todo: IP (v4/v6), fecha, meses en inglés sin
depender del locale del sistema, estado 100-599, bytes no negativos (`-` =
0) y codificación UTF-8 estricta. Una línea inválida devuelve `None` sin
abortar nada.

### `src/loghunter/consultas.py` (#4)

Agregadores combinables de `top-ips`, `status`, `urls` y `traffic`: cada
tramo produce un `Counter` parcial, y sumarlos (`reducir`) da el total —
así el cómputo se puede repartir entre procesos sin perder exactitud.
`presentar()` aplica el orden y el `--top` final (solo afecta `top-ips`/
`urls`). `calcular_referencia()` recorre el archivo completo en un solo
proceso, sin fragmentar: es el "oráculo" contra el que se compara el
resultado distribuido en los tests.

### `src/loghunter/generar.py` (#5)

Genera un log combined reproducible: misma semilla + mismos argumentos →
mismos bytes, siempre. Escribe por bloques (nunca carga el archivo entero
en RAM), con fecha base fija y PRNG propio (no depende de la hora de
ejecución). Permite pedir tamaño objetivo, cantidad de IPs, porcentaje de
líneas corruptas y semilla. El destino `s3://` todavía no está soportado
(queda explícito con `NotImplementedError`, pendiente del issue #12).

### `src/loghunter/lector.py` (#6)

Da acceso a rangos de bytes `[inicio, fin)` de un archivo local usando
`mmap` de solo lectura, sin cargar el archivo completo en memoria. Alinea
los offsets a `mmap.ALLOCATIONGRANULARITY` (requisito técnico del sistema
operativo) de forma transparente. Nunca intenta mapear un archivo vacío.
`leer_hasta_salto()` es la lectura auxiliar que usan los workers para
avanzar en bloques de 64 KiB hasta encontrar un salto de línea. El backend
`s3://` queda pendiente del issue #11.

### `src/loghunter/workers.py` (#7)

El corazón del map-reduce. Dos funciones puras:

- `calcular_rangos(tamaño, chunk_bytes)`: divide el archivo en tramos por
  aritmética simple, sin leer ni un byte de contenido.
- `map_chunk(uri, inicio, fin, query)`: dado un tramo, lo alinea a fin de
  línea (si el corte cae en medio de un registro, lo descarta y empieza en
  el siguiente; si una línea se extiende más allá del tramo, la termina de
  leer igual), parsea cada línea y devuelve el contador parcial más los
  totales de válidas/inválidas. Es la función que la tarea Celery del
  issue #10 va a reusar sin cambios.

### `tests/` (#3-#8)

Una suite de pruebas por módulo (`test_parser.py`, `test_consultas.py`,
`test_generar.py`, `test_lector.py`, `test_workers.py`) más
`test_integracion_nucleo.py`, que es la prueba de fondo del issue #8: genera
un log con `generar.py`, lo procesa por tramos con `workers.py` y compara
contra la referencia de `consultas.py`, para tramos de 64 KiB, 1 MiB y un
tamaño deliberadamente "raro" (700003 bytes), más archivo vacío y archivo
totalmente corrupto. 91 pruebas en total, todas en verde.

### `doc/VERIFICACION_NUCLEO.md` (#8)

Comandos y resultados reales (no hipotéticos) de la verificación, incluida
una corrida manual fuera de pytest.

### `README.md` / `INSTALL.md`

Actualizados en la sección "Estado": ya no dicen que el análisis está "en
desarrollo" sin más — ahora listan qué partes del núcleo local están
implementadas y verificadas, y cuáles siguen pendientes (cliente,
coordinador, tasks/Celery, indexador).

## Qué NO incluye esta etapa

Distribución con Celery/Redis/MinIO (#9-#14), cliente-servidor con asyncio
(#15-#19), persistencia e IPC (#20-#22), Docker Compose y entrega final
(#23-#28). Esos son los milestones 3 a 6.

## Referencias

PROJECT_STATEMENT §§7, 8 y 12.3; CU-02 a CU-06, CU-11, CU-12 y CU-24;
AC-01 a AC-04 y AC-16; ADR-0001, ADR-0004, ADR-0005, ADR-0011 y ADR-0013.

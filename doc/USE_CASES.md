# LogHunter — casos de uso

Fecha: 2026-11-02 (actualizado 2026-11-10). Estado: comportamiento esperado,
implementado y verificado — la matriz de evidencia por criterio de aceptación
está en `doc/VERIFICACION_INTEGRACION.md`.
Especificación normativa: [PROJECT_STATEMENT.md](PROJECT_STATEMENT.md).
Decisiones y fundamentos: [adr/INDEX.md](adr/INDEX.md).

## 1. Actores y convenciones

| Actor | Objetivo |
|---|---|
| Analista | Pedir estadísticas de un log y obtener un informe interpretable |
| Operador | Preparar servicios/logs, ajustar capacidad, inspeccionar y apagar |
| Desarrollador/evaluador | Comprobar corrección, distribución y rendimiento |
| Herramienta de consola | Consumir la salida JSON del cliente |
| Servicios secundarios | Redis, MinIO y filesystem, que proporcionan cola o datos |

El límite del sistema comprende cliente, coordinador, workers, lector, generador
e indexador; los componentes internos participan en los flujos, pero no representan
personas que inician acciones. Los casos técnicos de recuperación detallan las
extensiones del análisis que el operador o analista debe poder observar.

Todos los comandos son ejemplos del contrato futuro. Para resultados pequeños se
usa el archivo de seis registros de PROJECT_STATEMENT §7.3. Los IDs `AC-xx`
remiten a los criterios de aceptación de esa especificación. Cada caso define
precondiciones, flujo principal, alternativas, resultado y evidencia de aceptación.

## 2. Catálogo

| ID | Caso | Actor principal |
|---|---|---|
| CU-01 | Solicitar un análisis y seguir su progreso | Analista |
| CU-02 | Consultar las IPs con más peticiones | Analista |
| CU-03 | Consultar distribución de códigos HTTP | Analista |
| CU-04 | Consultar las URLs más solicitadas | Analista |
| CU-05 | Consultar bytes transferidos por hora | Analista |
| CU-06 | Analizar un archivo local | Analista |
| CU-07 | Analizar un objeto remoto | Analista |
| CU-08 | Ejecutar análisis concurrentes independientes | Varios analistas |
| CU-09 | Esperar cupo de análisis | Analista |
| CU-10 | Consumir el informe como JSON | Herramienta de consola |
| CU-11 | Generar un log reproducible | Operador |
| CU-12 | Analizar registros inválidos sin abortar | Analista |
| CU-13 | Procesar trabajo tras ausencia o pérdida de workers | Operador |
| CU-14 | Finalizar un análisis por timeout | Analista |
| CU-15 | Recibir errores de validación o infraestructura | Analista |
| CU-16 | Guardar el histórico del análisis | Analista |
| CU-17 | Consultar informes guardados | Operador |
| CU-18 | Inspeccionar trabajos mediante señal | Operador |
| CU-19 | Apagar ordenadamente el coordinador | Operador |
| CU-20 | Desplegar y escalar el procesamiento | Operador |
| CU-21 | Medir aceleración y verificar resultados | Desarrollador/evaluador |
| CU-22 | Limpiar un análisis cuyo cliente se desconectó | Analista |
| CU-23 | Detectar una caída del indexador | Operador |
| CU-24 | Analizar un archivo vacío y límites de registros | Analista |

## 3. Análisis y consultas

### CU-01 — Solicitar un análisis y seguir su progreso

**Actor:** analista. **Disparador:** ejecuta el cliente con archivo y consulta.
**Precondiciones:** coordinador disponible; archivo inmutable y accesible para el
backend elegido; Redis y al menos un worker disponibles para el flujo exitoso.

**Flujo principal:**

1. El cliente valida argumentos y convierte el destino a URI completa.
2. Abre conexión TCP IPv4 o IPv6 y envía el pedido JSON con LF.
3. El coordinador valida el pedido, asigna `job_id` e informa su aceptación.
4. Obtiene cupo, consulta el tamaño y calcula rangos por aritmética.
5. Publica un grupo de tareas y comunica la cantidad de tramos.
6. Los workers leen, alinean y procesan sus rangos; devuelven parciales.
7. El cliente recibe progreso de tramos exitosos, sin bloquear otros pedidos.
8. El coordinador comprueba éxito de todos, suma parciales, encola el agregado
   completo para persistir y devuelve la vista solicitada.
9. El cliente muestra tabla y métricas, cierra y termina con código 0.

**Alternativas:** espera de cupo → CU-09; error → CU-15; timeout → CU-14;
desconexión → CU-22; vacío → CU-24. Ninguna alternativa produce un informe de
éxito parcial presentado como completo.

**Resultado:** un informe asociado al pedido y recursos del job liberados.
**Aceptación:** AC-01, AC-14, AC-15; un solo terminal por conexión disponible.

### CU-02 — Consultar las IPs con más peticiones

**Actor:** analista. **Precondiciones:** las de CU-01.
**Disparador:** `python -m loghunter.cliente --file s3://logs/access.log --query top-ips --top 2`.

**Flujo principal:**

1. Ejecutar CU-01 contando una ocurrencia por IP de cada registro válido.
2. Sumar todos los contadores completos en el coordinador.
3. Ordenar por cantidad descendente y clave ascendente en empates; mostrar dos.

**Alternativas:** si hay menos de dos IPs, mostrar las disponibles; `--top 0`
se rechaza antes de conectar. No seleccionar el top por tramo.
**Resultado de ejemplo:** `10.0.0.1 → 3`, `10.0.0.2 → 2`.
**Aceptación:** AC-01; ranking idéntico al conteo secuencial incluso si una IP es
frecuente globalmente sin ser primera en un tramo.

### CU-03 — Consultar distribución de códigos HTTP

**Actor:** analista. **Precondiciones:** las de CU-01.
**Disparador:** cliente con `--query status`.

**Flujo principal:**

1. Ejecutar CU-01 agregando una ocurrencia por código HTTP válido.
2. Mostrar todos los códigos observados en orden numérico ascendente.
3. Presentar cantidad de líneas válidas e inválidas junto a la distribución.

**Alternativas:** `--top` no recorta esta distribución; códigos malformados o
fuera de 100–599 hacen inválido el registro. Sin válidas, distribución vacía.
**Resultado de ejemplo:** `200 → 3`, `404 → 1`, `500 → 1`.
**Aceptación:** AC-01; suma de cantidades igual a `lineas`.

### CU-04 — Consultar las URLs más solicitadas

**Actor:** analista. **Precondiciones:** las de CU-01.
**Disparador:** cliente con `--query urls --top 3`.

**Flujo principal:**

1. Ejecutar CU-01 contando el destino solicitado de cada registro válido.
2. Conservar query string y representación original del destino.
3. Agregar, ordenar por frecuencia descendente y clave ascendente, aplicar top.

**Alternativas:** `/a` y `/a?x=1` permanecen como claves diferentes. No agregar
normalización de rutas ni agrupación por método HTTP.
**Resultado de ejemplo:** `/a → 3`, `/a?x=1 → 1`, `/b → 1`.
**Aceptación:** AC-01; top global determinista y agregado completo en histórico.

### CU-05 — Consultar bytes transferidos por hora

**Actor:** analista. **Precondiciones:** las de CU-01; timestamps con offset válido.
**Disparador:** cliente con `--query traffic`.

**Flujo principal:**

1. Convertir cada timestamp válido a UTC y truncarlo al inicio de su hora.
2. Sumar los bytes por clave fecha-hora; contabilizar `-` como cero bytes.
3. Agregar todos los tramos y mostrar horas observadas en orden cronológico.

**Alternativas:** horas de días distintos no se mezclan; `--top` no recorta la
serie; bytes negativos o fechas inválidas convierten el registro en inválido.
**Resultado de ejemplo:** `2026-10-20T17:00:00Z → 350` y
`2026-10-20T18:00:00Z → 300`.
**Aceptación:** AC-01; suma de bytes y conversión de zonas verificadas.

### CU-06 — Analizar un archivo local

**Actor:** analista. **Precondiciones:** archivo accesible en el host del
coordinador y los workers; se utiliza el modo de una máquina.
**Disparador:** cliente con `--file ./datos/access.log --query status`.

**Flujo principal:**

1. El cliente convierte la ruta relativa a `local:///ruta/absoluta/datos/access.log`.
2. El coordinador obtiene el tamaño por metadatos, sin leer registros.
3. Los workers usan el backend `mmap`, alinean registros y cuentan.
4. Se completa CU-01 con el resultado agregado.

**Alternativas:** una ruta válida solo en el equipo del cliente no implica
disponibilidad en el servidor; informar acceso inexistente. No subir automáticamente
el archivo ni introducir un volumen compartido obligatorio.
**Resultado:** análisis local que utiliza `mmap` sin cargar todo el log por worker.
**Aceptación:** AC-02, AC-03; rangos no alineados a páginas también funcionan.

### CU-07 — Analizar un objeto remoto

**Actor:** analista. **Precondiciones:** bucket y objeto existentes; endpoint y
credenciales disponibles en coordinador y workers; log inmutable.
**Disparador:** cliente con `--file s3://logs/access.log --query top-ips`.

**Flujo principal:**

1. El coordinador obtiene el tamaño con `HEAD` y calcula rangos.
2. Cada worker traduce el rango semiabierto a `GET Range` inclusivo en HTTP.
3. Lee solo su rango y los bytes auxiliares de alineación; procesa y devuelve parcial.
4. Se completa CU-01, aunque un worker esté en otra máquina.

**Alternativas:** fallo transitorio → reintentos finitos; credenciales incorrectas,
objeto ausente o error persistente → CU-15. No descargar todo el objeto por tarea.
**Resultado:** análisis remoto sin dependencia de una ruta local común.
**Aceptación:** AC-02, AC-08; evidencia de rangos solicitados y worker remoto.

## 4. Concurrencia e interfaces

### CU-08 — Ejecutar análisis concurrentes independientes

**Actores:** dos o más analistas. **Precondiciones:** `max_jobs >= 2` para que al
menos dos estén activos; capacidad de workers disponible.
**Disparador:** A pide `top-ips` y B pide `status`, simultáneamente.

**Flujo principal:**

1. El coordinador acepta ambas conexiones en el mismo event loop.
2. Crea distintos `job_id` y `GroupResult`.
3. Alterna esperas de red/resultados y comunica progreso a cada socket.
4. Reduce cada grupo por separado y responde a su solicitante.

**Alternativas:** si uno falla o vence, solo se limpia su grupo; el otro conserva
su ejecución. Si no hay cupo suficiente, aplicar CU-09 al que espera.
**Resultado:** informes correctos sin intercambio ni mezcla de parciales.
**Aceptación:** AC-05, AC-11; las dos conexiones reciben atención durante el cómputo.

### CU-09 — Esperar cupo de análisis

**Actor:** analista. **Precondiciones:** todos los permisos de `max_jobs` ocupados.
**Disparador:** llega otro pedido válido.

**Flujo principal:**

1. El coordinador registra el pedido y envía `accepted` con `state: queued`.
2. Espera el semáforo sin bloquear el loop ni publicar tareas de ese pedido.
3. Cuando se libera un permiso, lo adquiere e inicia el timeout del job.
4. Ejecuta CU-01 y libera el permiso incluso si hay error.

**Alternativas:** cliente desconectado → CU-22; apagado → CU-19; no prometer una
posición o política de prioridad que la implementación no provee.
**Resultado:** nunca se supera el tope de análisis activos.
**Aceptación:** AC-06; con tope 1, B arranca cuando A termina o libera su cupo.

### CU-10 — Consumir el informe como JSON

**Actores:** analista y herramienta de consola. **Precondiciones:** las de CU-01.
**Disparador:** cliente con `--raw` y stdout redirigido a un archivo o pipe.

**Flujo principal:**

1. El cliente ejecuta el análisis sin imprimir tablas en stdout.
2. Envía progreso y diagnósticos a stderr.
3. Escribe exactamente el objeto JSON terminal más LF en stdout.
4. La herramienta analiza el JSON y el código de salida del proceso.

**Alternativas:** error del servidor produce JSON terminal de error y código 1;
error local de argumentos usa stderr y código 2; no se exige JSON si no hubo
respuesta del servidor por un fallo de conexión.
**Resultado:** salida procesable sin texto de barra de progreso mezclado.
**Aceptación:** AC-14; el contenido completo de stdout de un éxito es JSON válido.

## 5. Datos de prueba y tolerancia a fallos

### CU-11 — Generar un log reproducible

**Actor:** operador. **Precondiciones:** destino escribible; si es S3, bucket y
credenciales preparados.
**Disparador:** `python -m loghunter.generar -o s3://logs/prueba.log --size-mb 128 --ips 5000 --corrupt-pct 5 --seed 42`.

**Flujo principal:**

1. Validar tamaño, cantidad de IPs, porcentaje, semilla y destino.
2. Generar registros combined con PRNG y fecha base reproducibles.
3. Variar IPs, estados, bytes y tiempos monótonos con picos horarios.
4. Inyectar corrupción pseudoaleatoria y escribir por bloques hasta el objetivo.
5. Completar la última línea, subir si corresponde e informar tamaño y cantidades.

**Alternativas:** tamaño cero genera vacío; porcentaje fuera de 0–100 o IPs no
positivas se rechazan; fallo de escritura/subida termina con error, no con éxito.
El porcentaje real puede diferir del solicitado por el muestreo.
**Resultado:** mismo contenido con mismos argumentos/semilla, en local y en S3.
**Aceptación:** AC-16; hashes iguales, totales correctos y sin cargar todo en RAM.

### CU-12 — Analizar registros inválidos sin abortar

**Actor:** analista. **Precondiciones:** log con registros válidos y corruptos.
**Disparador:** cualquiera de las cuatro consultas.

**Flujo principal:**

1. El worker delimita cada registro completo antes de parsearlo.
2. Si falla el formato, codificación o validación, incrementa `invalidas` y continúa.
3. Solo los válidos incrementan `lineas` y aportan al contador de la consulta.
4. El coordinador suma ambos totales y los presenta en el informe.

**Alternativas:** si todos son inválidos, informe exitoso con resultados vacíos;
un error de almacenamiento no se confunde con una línea corrupta y va a CU-15.
**Resultado de ejemplo:** 5 válidas, 1 inválida y las estadísticas de §7.3.
**Aceptación:** AC-01, AC-04; no se pierde ni duplica el registro corrupto al cortar.

### CU-13 — Procesar trabajo tras ausencia o pérdida de workers

**Actor:** operador. **Precondiciones:** Redis disponible; timeouts/redelivery
configurados y log inmutable.
**Disparador:** hay tareas publicadas sin ejecutores disponibles o se pierde uno.

**Flujo principal, ausencia inicial:**

1. El analista inicia CU-01 sin workers activos.
2. Las tareas permanecen en Redis y el coordinador sigue atendiendo clientes.
3. El operador inicia un worker antes del deadline.
4. El worker consume las tareas y el análisis termina.

**Alternativa, pérdida durante ejecución:** configurar confirmación tardía/rechazo
al perder ejecutor; simular su caída; permitir reentrega dentro del plazo y
comprobar que el agregado usa una sola vez cada task ID. Una caída total del nodo
puede requerir esperar visibility timeout. Si no se recupera antes del deadline,
aplicar CU-14; un fallo permanente termina con CU-15.
**Resultado:** recuperación acotada, sin duplicar conteos ni prometer reintento infinito.
**Aceptación:** AC-07, AC-09; resultado igual a la corrida sin caída.

### CU-14 — Finalizar un análisis por timeout

**Actor:** analista. **Precondiciones:** job admitido que no completa dentro de
`--job-timeout`; otros jobs pueden estar activos.
**Disparador:** vence el deadline monotónico.

**Flujo principal:**

1. El coordinador marca el job `timed_out` y envía `JOB_TIMEOUT` si puede.
2. Revoca sus tareas pendientes, sin revocar grupos de otros clientes.
3. Deja de esperar/sumar sus parciales e ignora respuestas tardías.
4. Libera semáforo, registro activo y recursos de supervisión del job.

**Alternativas:** una tarea ya ejecutándose puede seguir hasta su límite; no se
afirma que `revoke()` detuvo el proceso. El hilo auxiliar de espera debe tener
timeouts propios para no quedar vivo indefinidamente.
**Resultado:** fallo terminal finito; otro pedido puede ocupar el cupo liberado.
**Aceptación:** AC-11; ningún informe parcial exitoso ni escritura de éxito para ese job.

### CU-15 — Recibir errores de validación o infraestructura

**Actor:** analista. **Precondiciones:** cliente invocado o conexión al servidor.
**Disparador:** argumento/pedido incorrecto o servicio/archivo inaccesible.

**Flujo principal:**

1. El cliente valida argumentos antes de abrir el socket.
2. El servidor vuelve a validar JSON, tamaño, versión, campos, consulta y URI.
3. Al acceder a servicios, distingue errores permanentes y transitorios.
4. Tras un fallo definitivo, envía el código aplicable, registra diagnóstico y
   limpia cualquier trabajo parcialmente publicado de ese job.
5. El cliente termina con código distinto de cero y mensaje claro.

**Variantes que deben ejercitarse:** puerto inválido, `top` no positivo, JSON
malformado/no objeto, pedido demasiado largo o sin LF, versión no soportada,
consulta desconocida, `.gz`, esquema desconocido, archivo/bucket inexistente,
permisos insuficientes, Redis caído, MinIO caído y un tramo en `FAILURE`.
No esperar 100% de éxitos si ya se conoce un fallo definitivo.
**Resultado:** error observable sin derribar el coordinador; si falla infraestructura
común, otros jobs también pueden fallar por esa misma causa, pero no se cancelan
arbitrariamente como si fueran el mismo pedido.
**Aceptación:** AC-10, AC-14, AC-15; ningún secreto o traceback en respuesta al cliente.

## 6. Persistencia y operación

### CU-16 — Guardar el histórico del análisis

**Actor beneficiario:** analista. **Disparador:** reduce completo y exitoso de CU-01.
**Precondiciones:** indexador vivo, cola disponible y destino SQLite escribible.

**Flujo principal:**

1. El coordinador encola metadatos, `job_id` y agregado completo antes del top.
2. El cliente recibe su vista con `persistencia: encolada`.
3. El indexador consume el informe e inicia una transacción.
4. Inserta informe y resultados, confirma y actualiza informes persistidos.

**Alternativas:** una entrega repetida del mismo `job_id` no duplica el informe;
error SQL revierte toda la transacción y queda registrado; caída del hijo → CU-23.
El éxito del análisis no confirma que SQLite ya haya hecho commit.
**Resultado:** una fila de informe y sus pares clave/valor, sin filas de logs crudos.
**Aceptación:** AC-12; un `--top 1` conserva también las otras claves en el histórico.

### CU-17 — Consultar informes guardados

**Actor:** operador. **Precondiciones:** SQLite con al menos un informe confirmado
y herramienta SQLite disponible.
**Disparador:** abre una conexión de lectura al archivo de histórico.

**Flujo principal:**

1. Ejecuta `sqlite3 -readonly informes.sqlite`.
2. Consulta `informes` por fecha, archivo o tipo de análisis.
3. Relaciona `resultados.informe_id` con `informes.id` para revisar el agregado.
4. Consulta duraciones/configuración para comparar corridas.

**Alternativas:** base sin informes → consulta sin filas; archivo inexistente →
error de apertura en lectura. No crear un endpoint o comando `history` implícito.
**Resultado:** acceso al histórico sin modificar la base ni competir como escritor.
**Aceptación:** AC-12; informe recuperable tras reiniciar el coordinador.

### CU-18 — Inspeccionar trabajos mediante señal

**Actor:** operador. **Precondiciones:** coordinador activo en entorno POSIX.
**Disparador:** `kill -USR1 <pid_coordinador>`.

**Flujo principal:**

1. El coordinador agenda el volcado de estado desde el handler.
2. Obtiene un snapshot de jobs y contadores con la sincronización correspondiente.
3. Imprime IDs, estados, progreso y tiempos sin detener el servicio.

**Alternativas:** sin jobs, informa cero activos; no debe esperar a Redis dentro
del handler ni corromper un contador al leerlo durante una actualización.
**Resultado:** diagnóstico visible sin abrir un puerto de administración.
**Aceptación:** AC-13; las consultas conectadas continúan recibiendo atención.

### CU-19 — Apagar ordenadamente el coordinador

**Actor:** operador. **Precondiciones:** coordinador activo; puede haber jobs en
ejecución, clientes en cola e informes pendientes de escritura.
**Disparador:** Ctrl+C, `SIGINT` o `SIGTERM`.

**Flujo principal:**

1. Dejar de aceptar conexiones y admitir jobs nuevos.
2. Informar `SERVER_SHUTDOWN` a clientes disponibles y cancelar jobs activos/en cola.
3. Revocar pendientes por grupo e ignorar resultados tardíos.
4. Detener productores y encolar centinela detrás de informes ya recibidos.
5. El indexador drena, confirma y cierra; el coordinador cierra cola y espera al hijo.
6. Liberar recursos y salir.

**Alternativas:** vencido el plazo de cierre, registrar fallo antes de forzar;
no afirmar drenaje completo en esa salida. No matar workers remotos compartidos.
**Resultado:** cierre normal sin informes encolados abandonados ni hijo huérfano.
**Aceptación:** AC-13; SQLite consistente y contadores de commit coherentes.

### CU-20 — Desplegar y escalar el procesamiento

**Actor:** operador. **Precondiciones:** Docker/Compose o entorno Python instalado,
configuración compartida, puertos disponibles y bucket preparado.
**Disparador:** iniciar el sistema o agregar capacidad.

**Flujo principal:**

1. Iniciar Redis, MinIO, coordinador y workers mediante Compose.
2. Comprobar disponibilidad y crear/cargar un log con CU-11.
3. Ejecutar CU-01 desde el cliente.
4. Agregar réplicas de worker o procesos `-c` sin reiniciar el coordinador.
5. Para distribución entre hosts, iniciar un worker remoto con las mismas URLs
   de Redis/backend/MinIO y credenciales compatibles.

**Alternativas:** `--scale worker=4` solo replica en el host de Compose; para
procesos totales multiplicar réplicas por `-c`. No usar el endpoint `localhost`
del coordinador como si resolviera al mismo servicio desde otra máquina.
**Resultado:** workers intercambiables procesan tareas sin copiar todo el log.
**Aceptación:** AC-08, AC-17; reinicio con volúmenes conserva logs e histórico.

### CU-21 — Medir aceleración y verificar resultados

**Actor:** desarrollador/evaluador. **Precondiciones:** archivo fijo, hash conocido,
configuración reproducible y capacidad suficiente; sin otros jobs simultáneos.
**Disparador:** preparar evidencia de paralelismo.

**Flujo principal:**

1. Fijar archivo, consulta, tramo, backend, versiones y entorno de ejecución.
2. Ejecutar al menos tres veces con un proceso de cómputo, luego con dos y cuatro.
3. Verificar que todas las corridas producen el mismo agregado y totales de líneas.
4. Registrar tiempos, procesos configurados/observados y condiciones de caché.
5. Calcular medianas y `T(1)/T(p)`; presentar tabla de resultados medidos.

**Alternativas:** si no mejora, identificar límites de CPU, red, almacenamiento o
cardinalidad; reportar las mediciones obtenidas y sus condiciones. Elegir
un archivo con suficientes tareas para utilizar la capacidad a medir.
**Resultado:** evidencia reproducible de corrección y rendimiento.
**Aceptación:** AC-18; cantidad de procesos explícita, sin confundir nodos con procesos.

### CU-22 — Limpiar un análisis cuyo cliente se desconectó

**Actor:** analista. **Precondiciones:** conexión aceptada con job en cola o activo.
**Disparador:** cierre del cliente o pérdida de conexión detectada por el servidor.

**Flujo principal:**

1. El coordinador detecta EOF/fallo de escritura y marca el job cancelado.
2. Si esperaba cupo, cancela su espera; si publicaba tareas, registra y revoca las
   ya publicadas; si estaba activo, revoca las pendientes de su grupo.
3. Ignora tardíos, libera cupo adquirido y retira supervisión/estado del job.

**Alternativas:** si el agregado ya se encoló para guardar antes de detectar la
desconexión, puede conservarse en el histórico; no retractar un commit válido.
No ofrecer reconexión o consulta posterior por `job_id` en esta versión.
**Resultado:** los demás clientes conservan sus jobs y no queda un permiso perdido.
**Aceptación:** AC-19; verificar desconexión tanto en espera como en ejecución.

### CU-23 — Detectar una caída del indexador

**Actor:** operador. **Precondiciones:** coordinador e indexador iniciados.
**Disparador:** el proceso hijo termina inesperadamente.

**Flujo principal:**

1. La supervisión detecta que el hijo murió y registra el incidente.
2. El coordinador deja de admitir nuevos análisis con `SERVICE_UNAVAILABLE`.
3. Los jobs todavía sin entregar un informe reciben fallo de servicio cuando
   intenten persistir; se limpian sin anunciar commit ni resultado exitoso nuevo.
4. El operador revisa logs/base y reinicia el servicio tras corregir la causa.

**Alternativas:** un resultado ya enviado con `persistencia: encolada` no puede
convertirse retroactivamente en confirmación ni en otro terminal por el socket.
Un error de transacción con hijo vivo se registra según CU-16; no equivale por sí
solo a una caída del proceso. No se exige reproducción durable de la cola perdida.
**Resultado:** fallo visible y finito, sin seguir acumulando informes sin consumidor.
**Aceptación:** AC-20; sin bloqueo indefinido por `put` ni contador falso de commit.

### CU-24 — Analizar un archivo vacío y límites de registros

**Actor:** analista. **Precondiciones:** archivo inmutable local o S3.
**Disparador:** análisis de vacío o archivo construido para ejercitar límites.

**Flujo principal para vacío:**

1. El coordinador obtiene tamaño cero.
2. No publica tareas ni intenta crear un `mmap` vacío.
3. Devuelve informe con cero tramos/procesos/líneas, resultados vacíos y lo encola.

**Variantes de delimitación:** corte dentro de una línea, sobre el byte LF,
inmediatamente después de LF, entre CR y LF, última línea sin LF, líneas de más
de 64 KiB y una línea que atraviesa varios tramos. Para cada variante, cada
registro pertenece al tramo que contiene su primer byte; los tramos intermedios
pueden devolver cero registros. Una línea vacía aporta una inválida.
**Resultado:** mismo conteo que el recorrido secuencial para cualquier corte.
**Aceptación:** AC-02, AC-03, AC-04; comparar ambos backends, sin pérdida ni duplicación.

## 7. Trazabilidad de aceptación

| Criterio | Casos principales |
|---|---|
| AC-01: consultas y referencia | CU-01 a CU-05, CU-12 |
| AC-02: igualdad de backends | CU-06, CU-07, CU-24 |
| AC-03: límites y propiedad de líneas | CU-06, CU-24 |
| AC-04: vacío/todo inválido | CU-12, CU-24 |
| AC-05: concurrencia | CU-08 |
| AC-06: admisión | CU-09 |
| AC-07: espera sin workers | CU-13 |
| AC-08: worker remoto | CU-07, CU-20 |
| AC-09: recuperación sin duplicación | CU-13 |
| AC-10: fallos finitos | CU-15 |
| AC-11: aislamiento de timeout | CU-08, CU-14 |
| AC-12: histórico transaccional | CU-16, CU-17 |
| AC-13: señales y cierre | CU-18, CU-19 |
| AC-14: CLI, raw e IP | CU-01, CU-10, CU-15 |
| AC-15: protocolo y framing | CU-01, CU-15 |
| AC-16: generación reproducible | CU-11 |
| AC-17: instalación y volúmenes | CU-20 |
| AC-18: mediciones | CU-21 |
| AC-19: desconexión | CU-22 |
| AC-20: indexador caído | CU-23 |

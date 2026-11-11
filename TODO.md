# TODO — mejoras futuras

LogHunter cumple el alcance acordado con la cátedra
([PROJECT_STATEMENT.md §3](doc/PROJECT_STATEMENT.md)); esta lista parte de lo
que ese mismo documento excluye explícitamente (§3.2) y de límites
reconocidos en las verificaciones reales (`doc/VERIFICACION_*.md`). No es
una promesa de implementación: es dónde seguiría el proyecto si continuara
más allá de la entrega. No implementar nada de esta lista sin que el
alcance se actualice primero en `PROJECT_STATEMENT.md` y su ADR.

## A partir de las exclusiones de PROJECT_STATEMENT §3.2

- **Autenticación/autorización de clientes.** Hoy el coordinador acepta
  cualquier conexión TCP en su red; el entorno previsto es de
  laboratorio/red controlada (§14). Agregar credenciales de cliente
  cambiaría el protocolo (ADR-0006) y el modelo de admisión (ADR-0007).
- **Interfaz web o gráfica.** El cliente es CLI por diseño (ADR-0010); una
  UI consumiría el mismo protocolo TCP/JSON sin tocar el coordinador.
- **Seguimiento en vivo (`tail -f`) o análisis de archivos en
  modificación.** El contrato asume que el log está completo e inmutable
  antes de analizarlo (§3.2); seguir un archivo creciendo exigiría
  invalidar el supuesto de "tamaño fijo conocido de antemano" en el que se
  apoya todo el particionado (ADR-0001, ADR-0005).
- **Formatos distintos del combined log de Apache/Nginx, o logs
  comprimidos (`.gz`).** El parser (ADR-0011) y el particionado por bytes
  (ADR-0005) asumen texto plano sin comprimir; un formato comprimido no se
  puede recortar por rango de bytes sin descomprimir desde el inicio del
  archivo, lo que rompe la base del map-reduce por rangos.
- **Replicación o alta disponibilidad de MinIO, Redis o el coordinador.**
  ADR-0012 es explícito: la cola no garantiza sobrevivir a la pérdida de
  Redis, y no hay HA del coordinador (punto único). Agregar HA real
  implicaría Redis Sentinel/Cluster, replicación de MinIO y algún mecanismo
  de coordinador activo-pasivo — cambios de infraestructura, no solo de
  código.
- **Recuperación de jobs tras reiniciar el coordinador, entrega
  exactamente una vez de extremo a extremo, y persistencia confirmada antes
  de responder al cliente.** Hoy el estado de los jobs vive en memoria del
  coordinador (`dict` bajo `asyncio.Lock`); un reinicio pierde los jobs en
  curso (sus clientes ven la conexión cerrada, no un resultado corrupto).
  El informe se persiste *después* de responder, por diseño (persistir
  dentro del camino de respuesta bloquearía el event loop, invariante 1):
  hay una ventana donde el cliente ya vio el resultado pero el indexador
  todavía no lo escribió. Resolver esto requeriría un log de jobs
  persistente (WAL propio o una tabla de "jobs en curso" en SQLite) y un
  protocolo de reconexión por `job_id`, ambos fuera del contrato actual.

## A partir de límites reconocidos en la verificación real

- **Escalar el benchmark más allá de 4 procesos.**
  `doc/VERIFICACION_BENCHMARK.md` mide 1/2/4 en un host de 11 núcleos y
  observa que la eficiencia marginal *mejoró* de 2→4 (amortización del
  costo fijo de round-trip por tarea); no se sabe si eso se sostiene a 8 o
  16 procesos, ni en un host con menos núcleos. Repetir el ensayo con más
  configuraciones y, idealmente, en más de una máquina física real (no
  contenedores en un solo host) daría una medición de distribución
  completa, separada de la de concurrencia de un solo nodo.
- **Agregación en disco para cardinalidad muy alta de claves.**
  PROJECT_STATEMENT §14 ya documenta que los contadores completos ocupan
  memoria proporcional a las claves distintas y que esta versión no
  implementa un motor de agregación en disco. Si el caso de uso previera
  URLs o IPs con cardinalidad no acotada, un `Counter` en memoria por job
  dejaría de ser viable.
- **Pruebas de fallos combinados.** `doc/VERIFICACION_INTEGRACION.md` señala
  que las señales reales, Redis/MinIO reales y la caída del indexador se
  verificaron reales pero por separado, no las tres condiciones
  combinadas en una sola corrida (p. ej. apagar el coordinador por señal
  mientras el indexador está caído y MinIO no responde). Cubrir esa
  combinación agregaría certeza marginal sobre interacciones que
  PROJECT_STATEMENT no describe como un escenario propio, al costo de una
  prueba frágil y lenta — se dejó fuera de la entrega por esa razón, no por
  falta de tiempo solamente.
- **Checkout limpio real en una máquina sin este repositorio.**
  `doc/VERIFICACION_INTEGRACION.md` documenta que el build de Compose sí se
  verificó desde un contexto de build limpio, pero el flujo completo
  `git clone` → `uv sync` → pruebas no se repitió en una máquina nueva
  separada de este entorno de desarrollo. `tests/test_imports.py` cubre la
  parte automatizable (el paquete instalado se importa desde otro
  directorio sin arrancar servicios).
- **Workers remotos entre máquinas físicas reales.** Todas las
  verificaciones de "worker en otro host" de este repositorio (milestone 3
  y el despliegue de Compose) usan un contenedor Docker separado en el
  mismo host físico como sustituto de una segunda máquina; es la evidencia
  disponible en este entorno de laboratorio (documentado explícitamente en
  cada verificación), no una demostración entre hosts físicos distintos.

## Mejoras de calidad que no cambian el contrato

- **Reintentos configurables para errores transitorios de MinIO en
  `generar.py`.** `lector.LectorS3` ya distingue errores transitorios de
  permanentes con backoff propio (ADR-0004); `generar.py` al subir un log
  no tiene la misma política.
- **Métrica de latencia por tarea, no solo contadores agregados.**
  `ipc.Metricas` cuenta jobs activos e informes emitidos/persistidos; no
  hay percentiles de tiempo por tarea Celery, que ayudarían a diagnosticar
  el cuello de botella de round-trip descrito en
  `doc/VERIFICACION_BENCHMARK.md` sin tener que correr el benchmark.

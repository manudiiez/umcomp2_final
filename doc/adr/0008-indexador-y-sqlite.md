# ADR-0008 — Indexador como único escritor de SQLite

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Se guardan informes, no millones de registros de log. La escritura SQL sincrónica
no debe detener la atención de clientes. La respuesta rápida y la confirmación
durable son garantías diferentes que deben explicitarse en el contrato.

## Decisión

Crear un proceso hijo indexador con conexión SQLite propia. Recibir informes por
`multiprocessing.Queue`, guardar metadatos y resultados completos en una transacción
y actualizar el contador persistido solo tras commit. Habilitar claves foráneas.

Agregar `job_id UNIQUE` para no duplicar entregas y guardar `top` y `chunk_mb`
efectivos junto con las métricas de ejecución. El cliente recibe la vista
recortada, pero el histórico guarda el agregado completo. La respuesta declara
`persistencia: encolada`, sin esperar un ack de commit. Consultar el histórico
mediante SQLite en modo lectura; no añadir una consulta remota `history`.

Supervisar el proceso hijo. Si cae, bloquear nuevas admisiones y fallar los jobs
que todavía necesitan entregarle informes. Registrar y revertir errores de
transacción; no contabilizarlos como persistidos. Cerrar normalmente con drenaje.

## Alternativas

- Escribir dentro del event loop: introduce esperas bloqueantes de disco.
- Hilo de escritura: técnicamente válido; el proceso permite aislarlo y demostrar IPC.
- PostgreSQL/MySQL: válidos con otros requisitos; aquí agregan un servidor para
  un volumen bajo y un único escritor.
- Esperar commit antes de responder o cola durable: mayor garantía, fuera del
  contrato actual de persistencia asincrónica.

## Consecuencias

Los informes ya encolados se drenan al cerrar normalmente. Una caída puede perder
informes aún no confirmados; `Queue.put` no proporciona durabilidad. La base
persiste al cerrar y reiniciar si se conserva su archivo/volumen. El top completo
puede ser grande si hay muchas claves distintas, aunque el cliente pida pocas.

## Verificación y referencias

CU-16, CU-17, CU-19 y CU-23; AC-12, AC-13 y AC-20. Verificar atomicidad, entrega
duplicada, top completo y caída del hijo. Esquema en PROJECT_STATEMENT §11;
garantías de persistencia en PROJECT_STATEMENT §11.

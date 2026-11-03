# ADR-0003 — Celery con Redis para tareas y resultados

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Los workers deben ser independientes del coordinador y poder ejecutarse en otros
hosts. Las tareas deben esperar si no hay ejecutores y sus resultados deben
recuperarse sin mezclar pedidos. La mera idempotencia de una función no configura
su reentrega tras una caída.

## Decisión

Usar Celery con pool `prefork`, Redis como broker y Redis como backend, con ambas
URLs explícitas. Encolar un `group` por job con una tarea por rango. Aceptar
serialización JSON en tareas y resultados. No enviar bytes del log por la cola.
Cada tarea devuelve su contador completo y totales; no persiste efectos propios.

Habilitar `task_acks_late` y `task_reject_on_worker_lost`; hacer reintentos finitos
con backoff para lecturas transitorias. Los errores permanentes fallan el job.
Usar task IDs para agregar una vez cada parcial lógico, aunque haya reejecuciones.
Configurar `time_limit`, expiración, visibility timeout y retención según
PROJECT_STATEMENT §9.3; comprobar su interacción con pruebas de caída.

## Alternativas

- `multiprocessing.Pool` / `ProcessPoolExecutor`: adecuados para procesos locales,
  pero no proporcionan por sí solos este broker y ejecución en otros hosts.
- RabbitMQ: alternativa válida de broker; para este proyecto Redis cubre también
  el almacenamiento de resultados con un solo servicio.
- Implementar una cola propia: exige resolver entrega, estado y supervisión sin
  aportar al objetivo del analizador.

## Consecuencias

Se asume posible reejecución de tareas, no ejecución exactamente una vez.
La recuperación depende de los límites configurados y del fallo concreto; no
prometer que toda pérdida se reentrega instantáneamente. Los reintentos de lectura
son finitos y el deadline de job evita esperas indefinidas. Redis puede ser un
punto único de fallo; no se incluye un clúster de alta disponibilidad.

## Verificación y referencias

CU-13 y CU-15; AC-07, AC-09 y AC-10. Comprobar ausencia inicial de workers y
pérdida durante ejecución, con conteos iguales a una corrida sin caída.

La [guía de tareas de Celery](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
explica confirmación tardía y pérdida del ejecutor. La
[configuración oficial](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
define sus opciones; la [guía de Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
explica visibility timeout. Verificar valores y comportamiento con las versiones fijadas.

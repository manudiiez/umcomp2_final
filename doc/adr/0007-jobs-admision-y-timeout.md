# ADR-0007 — Identidad, admisión y finalización de jobs

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Varios clientes generan tareas al mismo tiempo. Se necesita saber qué parciales
pertenecen a cada pedido, cuándo puede reducirse y cómo impedir que un análisis
colgado consuma recursos sin límite.

## Decisión

Asignar UUID a cada pedido válido y un `GroupResult` al grupo de tareas de ese
job. Mantener sus task IDs y socket en el registro activo. Usar semáforo con
`max_jobs=3` por defecto; informar espera antes de admitir más cómputo.

Iniciar deadline monotónico al adquirir cupo; default 300 s. Publicar progreso
por tramos exitosos y comprobar fallo del grupo antes de reducir. La lista de
estados y el alcance temporal están definidos en PROJECT_STATEMENT §9.

En timeout, fallo terminal, desconexión detectada o apagado, revocar pendientes
de ese job, ignorar tardíos y limpiar en `finally`. No matar globalmente procesos
worker. El límite duro acota una tarea que ya está ejecutándose.

## Alternativas

- Bolsa común de parciales sin identidad: permite mezclar resultados.
- Contador manual comprobado antes de un `await`: puede admitir más del máximo.
- Esperar siempre 100% de `completed_count()`: puede esperar hasta timeout si una
  tarea falló; un grupo listo tampoco garantiza éxito.
- Cancelación global de workers: afecta pedidos independientes.

## Consecuencias

Un job lento no debe cancelar otro. Liberar el permiso no implica que una tarea
remota activa ya se detuvo. No hay recuperación del registro de jobs tras reiniciar
el coordinador. Las esperas sincrónicas delegadas deben tener límites propios.

## Verificación y referencias

CU-08, CU-09, CU-14 y CU-22; AC-05, AC-06, AC-11 y AC-19.
La [referencia de resultados de Celery](https://docs.celeryq.dev/en/stable/reference/celery.result.html)
distingue tareas exitosas de grupo listo; la
[guía de workers](https://docs.celeryq.dev/en/stable/userguide/workers.html#revoke-revoking-tasks)
explica revocación y sus límites. Esas APIs no sustituyen el estado terminal del job.

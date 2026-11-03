# ADR-0002 — Coordinador con asyncio

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

El coordinador espera conexiones, metadatos y resultados; el parseo intensivo
ocurre en los workers. Una espera sincrónica en el event loop demoraría a todos
los clientes. Un único hilo tampoco evita carreras lógicas entre corrutinas si
estas leen estado, ceden control y después actúan sobre una lectura desactualizada.

## Decisión

Usar `asyncio.start_server`, un event loop y corrutinas por conexión. Mantener
el estado de jobs en el hilo del loop. Ejecutar llamadas bloqueantes a Celery,
Redis, boto3, colas o `join()` en hilos auxiliares mediante `asyncio.to_thread`
o en procesos dedicados, según su responsabilidad.

Las esperas auxiliares deben tener timeout: cancelar su corrutina no garantiza
interrumpir la llamada que corre en un hilo. El reduce y la serialización tampoco
deben producir pausas largas; usar lotes con cesión o cálculo delegado sin
modificar estado compartido del coordinador desde el hilo auxiliar.

No colocar `await` entre lectura y modificación de una invariante sin protegerla.
Usar `asyncio.Lock` donde la sección necesita esperar y `Semaphore` para admisión.

## Alternativas

- Un hilo por cliente: viable para I/O, pero exige sincronizar el estado entre
  hilos y no es el modelo elegido para demostrar asincronismo de I/O.
- Un proceso por cliente: más aislamiento y más costo para una función de espera.
- Ejecutar el parseo en el loop: ocuparía el hilo y afectaría todas las conexiones.

## Consecuencias

La atención de sockets se mantiene concurrente con un solo hilo del loop; existen
hilos auxiliares, por lo que no debe describirse todo el proceso como estrictamente
monohilo. El beneficio frente a hilos es organización y control de cesiones; no
afirmar que los hilos nunca sirven para I/O ni que el GIL impide esperar en paralelo.

## Verificación y referencias

Dos clientes reciben atención durante esperas y fallos. Revisar todas las rutas
que acceden a servicios y las secciones críticas. CU-08, CU-09; AC-05, AC-06.
La [documentación de asyncio](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
describe la delegación de I/O bloqueante a hilos; la obligación de aplicarla a
estos componentes es una decisión del proyecto.

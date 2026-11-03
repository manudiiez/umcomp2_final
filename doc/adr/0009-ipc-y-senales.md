# ADR-0009 — Cola, contadores sincronizados y señales POSIX

- Fecha: 2026-11-02.
- Estado: adoptado para implementación.
- Referencia: [PROJECT_STATEMENT.md](../PROJECT_STATEMENT.md).

## Contexto

Coordinador e indexador son procesos distintos. Necesitan pasar informes y observar
contadores; las variables normales no constituyen estado compartido entre procesos.
También hace falta inspección y cierre sin construir una API administrativa.

## Decisión

Usar `multiprocessing.Queue` para informes y centinela final, y `Value` con `Lock`
para métricas escalares. Proteger lecturas y escrituras, incluido `+=`, y asignar
un responsable por métrica según PROJECT_STATEMENT §11.

Usar `SIGUSR1` para agendar snapshot y `SIGINT`/`SIGTERM` para iniciar el cierre.
El handler no realiza I/O bloqueante ni espera locks largos. Al cerrar, detener
productores, colocar centinela tras los informes, drenar, cerrar cola y esperar
al hijo con `Process.join()` fuera del loop. Default de plazo de cierre: 30 s.
No depender de conexiones heredadas ni de un método de inicio de procesos implícito.

## Alternativas

- Variables ordinarias: no comparten sus modificaciones entre procesos.
- `Value` sin lock en read-modify-write: permite actualizaciones perdidas.
- Pipe manual: exige resolver serialización, delimitación y sincronización.
- Endpoint de administración: amplía superficie y protocolo sin ser necesario.
- Matar inmediatamente al hijo: arriesga informes pendientes y recursos de cola.

## Consecuencias

Se demuestran mecanismos IPC locales distintos del broker distribuido. Los locks
entre procesos y los de asyncio resuelven problemas diferentes. El soporte de
señales elegido requiere entorno POSIX. La terminación forzada es un último recurso
registrado, no un cierre que pueda prometer drenaje completo.

## Verificación y referencias

CU-18 y CU-19; AC-13. Comprobar contadores con múltiples jobs y cierre con cola
pendiente. La [documentación de multiprocessing](https://docs.python.org/3/library/multiprocessing.html)
describe procesos, memoria compartida, colas y sus precauciones de terminación.

# Registro de decisiones de arquitectura y desarrollo

Un ADR (*Architecture Decision Record*) registra una decisión, su contexto,
alternativas y consecuencias. Estos documentos acompañan la especificación de
[LogHunter](../PROJECT_STATEMENT.md) y sus [casos de uso](../USE_CASES.md).

Fecha de consolidación: 2026-11-02. Estado de los ADR siguientes: **adoptado para
la implementación**; todavía no existe código que materialice las decisiones.
Cada decisión contiene su contexto y justificación técnica, y se relaciona con
la especificación y los casos de uso vigentes.

## Índice

| ADR | Decisión |
|---|---|
| [0001](0001-map-reduce-y-alcance.md) | Resolver estadísticas de logs con map-reduce y alcance acotado |
| [0002](0002-coordinador-asyncio.md) | Usar asyncio en el coordinador y aislar llamadas bloqueantes |
| [0003](0003-celery-y-redis.md) | Distribuir tareas con Celery y Redis, con recuperación configurada |
| [0004](0004-lector-local-y-s3.md) | Ofrecer lectura por rango con mmap local y API S3 de MinIO |
| [0005](0005-particionado-y-lineas.md) | Asignar registros por su offset inicial y alinear en el worker |
| [0006](0006-protocolo-tcp-json.md) | Usar TCP con JSON delimitado por LF |
| [0007](0007-jobs-admision-y-timeout.md) | Correlacionar jobs con UUID/GroupResult y limitar su ciclo de vida |
| [0008](0008-indexador-y-sqlite.md) | Persistir informes mediante un proceso indexador y SQLite |
| [0009](0009-ipc-y-senales.md) | Usar cola, contadores sincronizados y señales POSIX para IPC local |
| [0010](0010-cli-y-configuracion.md) | Ofrecer CLI con argparse y configuración explícita compartida |
| [0011](0011-formato-y-consultas.md) | Aislar el parser combined y fijar semántica de agregación |
| [0012](0012-despliegue-y-entorno.md) | Desplegar con Docker Compose y permitir workers remotos |
| [0013](0013-desarrollo-y-verificacion.md) | Desarrollar incrementalmente y verificar con datos reproducibles |
| [0014](0014-flujo-github-por-milestones.md) | Asociar issues a milestones e integrar solo mediante PRs |

## Mantenimiento

Cada ADR contiene estado, contexto, decisión, alternativas, consecuencias
y verificación. Las referencias externas son documentación técnica primaria
consultada para precisar contratos; las versiones realmente usadas se fijarán
al implementar y se registrarán en el entorno del proyecto.

No renumerar ADR publicados ni reutilizar números. Para una decisión distinta,
crear el siguiente número, enlazar el antecedente y marcarlo reemplazado por el
nuevo ADR. Un cambio editorial puede corregir el mismo archivo. Las opciones
operativas, como ajustar un timeout dentro del modelo acordado, se documentan en
configuración; una sustitución de Celery, almacenamiento o modelo de concurrencia
requiere revisar el ADR y la especificación.

Los ADR explican el porqué; PROJECT_STATEMENT define el contrato observable y
USE_CASES sus escenarios. Si se detecta una contradicción, corregir los documentos
afectados antes de implementar conductas incompatibles, sin ampliar el alcance
silenciosamente.

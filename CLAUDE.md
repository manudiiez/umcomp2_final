# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es este repositorio

Proyecto final de **Computación II** (Universidad de Mendoza, Sede San Rafael):
**LogHunter**, un analizador distribuido de logs. La materia evalúa el uso correcto
de sockets concurrentes, IPC, asincronismo de I/O y colas de tareas distribuidas,
por lo que **los mecanismos elegidos son el objeto de la evaluación**, no solo el
resultado funcional.

**Estado actual: no hay código.** La arquitectura figura como aprobada en principio
con los ajustes documentados. El repositorio contiene la especificación consolidada,
casos de uso y ADR en `doc/`.

La documentación y los mensajes de commit están en **castellano**.

## Documentos de referencia obligatorios

Antes de escribir código, leer:

| Archivo | Qué define |
|---|---|
| `doc/PROJECT_STATEMENT.md` | **Contrato vigente de implementación**: alcance, arquitectura, protocolos, datos, CLI, fallos y aceptación |
| `doc/USE_CASES.md` | Flujos, alternativas, errores y resultados verificables de cada funcionalidad |
| `doc/DEVELOPMENT_PLAN.md` | Etapas técnicas, issues reales, dependencias y seguimiento de avances |
| `doc/adr/INDEX.md` | Índice de decisiones, razones, alternativas y consecuencias |
| `../UM_C2/FINAL.md` | Requisitos de la cátedra (fuera de este repo) |

`doc/PROJECT_STATEMENT.md` define el **contrato** de desarrollo. Aplicar sus
contratos exactos junto con los casos de uso y sus ADR.
No agregar funcionalidad excluida sin que el usuario lo pida explícitamente.

La arquitectura permite ejecutar los workers en máquinas independientes del
almacenamiento del log. El contrato de lectura por rangos y sus fundamentos se
encuentran en `doc/PROJECT_STATEMENT.md` §8 y ADR-0004/ADR-0005.

## Arquitectura

Map-reduce sobre un archivo de log, con dos modelos de concurrencia conviviendo a
propósito:

```
cliente.py  ──TCP:8888──►  coordinador.py  ──Celery/Redis──►  workers (map_chunk)
 (CLI)                      (asyncio)                          (N máquinas)
                                 │                                  │
                      multiprocessing.Queue              lector.py  │
                                 ▼                     ┌────────────┴───────────┐
                           indexador.py ──► SQLite     │ local:// → mmap        │
                                                       │ s3://    → GET Range   │
                                                       └────────────┬───────────┘
                                                                    ▼
                                                        MinIO (API S3) :9000
```

- **`coordinador.py`** — servidor `asyncio`, un proceso y un hilo de event loop,
  con hilos auxiliares para llamadas bloqueantes. Atiende N
  clientes concurrentes. **No cuenta nada y no lee el log**: pide el tamaño al
  `lector`, calcula los cortes por aritmética, encola una tarea por tramo y hace el
  reduce de los parciales. Correlaciona con `job_id` + `GroupResult`.
- **workers Celery** (`tasks.py`) — procesos en máquinas independientes. Ejecutan
  `map_chunk(uri, inicio, fin, query, job_id)`: piden **solo su tramo** al `lector`,
  lo alinean a fin de línea, parsean y devuelven un `Counter`. Sin estado,
  idempotentes.
- **`lector.py`** — abstracción de lectura por rango: `tamano()` y
  `leer(inicio, fin)`, con backend `local://` (`mmap`) y `s3://` (`GET Range` contra
  MinIO). Es lo que permite que los workers no vivan en la máquina del log.
- **`indexador.py`** — proceso hijo del coordinador. Único escritor de SQLite.
- **`generar.py`** — genera logs sintéticos de tamaño controlado.

### Por qué dos modelos y no uno

El coordinador es **I/O-bound** (espera sockets y resultados) → `asyncio`, que
escala sin el costo de hilos o procesos y sin *data races*. Los workers
son **CPU-bound** (regex sobre millones de líneas) → procesos separados, porque el
GIL impide paralelizar cálculo con hilos. Esta distinción es el aporte conceptual
del proyecto y está justificada en ADR-0002 y ADR-0003.

## Invariantes de diseño

Violar cualquiera de estos rompe la justificación del proyecto ante el tribunal:

1. **Nunca bloquear el event loop del coordinador.** Toda llamada bloqueante
   (`AsyncResult.get()` de Celery, `sqlite3`, I/O sincrónico) va fuera: con
   `asyncio.to_thread`, o delegada al indexador. Un `INSERT` lento dentro del loop
   congela la atención de todos los clientes.
2. **Por la cola viajan coordenadas, no datos.** Se encola `(uri, inicio, fin)`,
   nunca los bytes del tramo. Mandar los datos significaría empujar GB por Redis.
   El worker resuelve el acceso por su cuenta a través de `lector.py`, así que
   **nada lo ata a la máquina del archivo** en el backend S3; ver ADR-0004.
3. **El coordinador no lee el contenido del log.** Solo pide el tamaño y divide. La
   alineación a fin de línea la hace **cada worker** sobre su tramo: si `inicio > 0`,
   consulta el byte previo y descarta solo una primera línea realmente parcial.
   Completa la última línea cuyo inicio pertenece a `[inicio, fin)`. La propiedad
   de los registros debe ser disjunta y cubrir el archivo completo; ver ADR-0005.
4. **Todo acceso a un `Value` compartido va bajo `Lock`.** `+= 1` es
   read-modify-write, no es atómico.
5. **Ninguna sección crítica cede control sin sincronización.** `asyncio`
   elimina las *data races*, no las carreras lógicas que cruzan un punto de cesión:
   el patrón *leer → `await` → escribir* está prohibido sobre el diccionario de jobs
   y los contadores de progreso. Si la sección necesita esperar, va con
   `asyncio.Lock`; el tope de jobs se resuelve con `asyncio.Semaphore`.
6. **`map_chunk` debe ser idempotente**, porque Celery reintenta tareas de workers
   caídos.
7. **El indexador es el único escritor de SQLite.** Es lo que permite usar SQLite
   en lugar de un motor con servidor.
8. **Cada job tiene su `job_id` y su `GroupResult`.** Los parciales nunca se
   mezclan entre pedidos simultáneos, el progreso usa `completed_count()` (éxitos),
   se comprueban fallos por separado, y un
   `--job-timeout` revoca las tareas pendientes de ese job sin afectar a los demás
   clientes.
9. **El protocolo cliente↔coordinador es JSON delimitado por `\n`, no `pickle`.**
   `pickle` ejecuta código al deserializar y el coordinador acepta conexiones de
   cualquiera.

## Comandos previstos

Interfaz consolidada en `doc/PROJECT_STATEMENT.md` §12. **Aún no implementada**; al
escribir el código respetar sus nombres de flags, defaults y precedencia.

```bash
# Infraestructura
docker run -d -p 6379:6379 redis
docker run -d -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address :9001

# Workers (el paralelismo se escala acá, sin tocar el coordinador;
# pueden correr en otra máquina apuntando al mismo Redis y al mismo MinIO)
celery -A loghunter.tasks worker --loglevel=INFO -c 4

# Coordinador
python -m loghunter.coordinador -i 0.0.0.0 -p 8888 --chunk-mb 32 --max-jobs 3 \
                               --job-timeout 300 --s3-endpoint http://localhost:9000

# Cliente (el esquema de la URI decide por dónde leen los workers)
python -m loghunter.cliente --file s3://logs/access.log --query top-ips --top 10 \
                            -i 127.0.0.1 -p 8888

# Generar un log de prueba reproducible
python -m loghunter.generar -o s3://logs/access.log --size-mb 2000 --seed 42

# Despliegue completo
docker compose up -d --scale worker=4
```

Consultas soportadas: `top-ips`, `status`, `urls`, `traffic`.

## Convenciones de trabajo

- Leer y aplicar `.claude/skills/flujo-github/SKILL.md` antes de gestionar cambios
  o GitHub. Conventional Commits obligatorios y todo issue asignado a un milestone.
- Ramas de issue desde su rama milestone. Integrar solo por PR:
  `rama del issue → rama del milestone → develop → main`.
- Nunca hacer commits de trabajo ni push directo a `main`, `develop` o ramas
  milestone existentes. El usuario revisa y mergea los PRs.
- **Commits frecuentes e incrementales.** La cátedra evalúa la progresión del
  repositorio, no solo el resultado. Commitear aunque el código todavía no
  funcione; no esperar a tener una versión completa.
- **Si cambian los requisitos, actualizar `doc/` y commitearlo.** `FINAL.md` lo
  exige explícitamente para los ajustes acordados con el profesor.
- Mensajes de commit en castellano, en imperativo, con el detalle en el cuerpo.

## Pendientes del repositorio

`FINAL.md` exige estos archivos para la presentación final; se escriben cuando haya
código:

- `INSTALL.md` — clonado, instalación y despliegue.
- `INFO.md` — decisiones de diseño y su justificación.
- `TODO.md` — mejoras y features futuras. Arrancar desde las exclusiones de
  `doc/PROJECT_STATEMENT.md` §3.2.
- `README.md` — ampliar con ayuda y uso básico (hoy es solo una portada).

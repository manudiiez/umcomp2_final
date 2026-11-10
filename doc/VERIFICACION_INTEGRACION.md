# Verificación de integración y despliegue completo (milestone 6, issue #25)

Matriz de evidencia de AC-01 a AC-20 (AC-18 es responsabilidad del benchmark
del issue #26, fuera de este issue) y confirmación de que el despliegue
Compose real (`docker-compose.yml`, issue #24) es el que se ejecutó para la
evidencia de extremo a extremo, no una configuración distinta.
PROJECT_STATEMENT §15; USE_CASES.md completo salvo CU-21 (benchmark); ADR-0013.

## Cómo leer la matriz

La columna "evidencia" apunta al archivo y prueba concretos. "infra real"
indica si esa prueba corre contra Redis/MinIO/Celery/SQLite reales (se salta
sola si no están disponibles, nunca se reemplaza por un doble) o si usa
dobles controlados porque el riesgo que cubre es de lógica, no de
infraestructura (ADR-0013: "los mocks por sí solos no demuestran
distribución ni recuperación", aplicado donde corresponde y no en todos
lados).

| AC | Evidencia | Infra real |
|---|---|---|
| AC-01 (consultas == referencia) | `tests/test_integracion_nucleo.py`, `tests/test_integracion_cliente_servidor.py::test_analisis_completo_con_redis_y_worker_reales` | Sí (segunda) |
| AC-02 (igualdad de backends) | `tests/test_integracion_distribucion.py::test_backend_local_distribuido_coincide_con_la_referencia`, `::test_backend_s3_distribuido_coincide_con_la_referencia_y_con_local` | Sí |
| AC-03 (propiedad de líneas/cortes) | `tests/test_workers.py` (CRLF, línea >64 KiB, línea que atraviesa tramos), `tests/test_integracion_nucleo.py` (tramos no alineados) | No (no depende de infraestructura externa, es aritmética/E-S local) |
| AC-04 (vacío/todo inválido) | `tests/test_workers.py`, `tests/test_coordinador.py::test_archivo_vacio_no_publica_tareas_y_responde_informe_vacio` | No |
| AC-05 (dos clientes sin mezcla) | `tests/test_coordinador.py::test_dos_clientes_concurrentes_no_mezclan_progreso_ni_resultados` (dobles) + verificación manual contra Compose más abajo (real) | Sí (la manual) |
| AC-06 (admisión con `max_jobs=1`) | `tests/test_coordinador.py::test_max_jobs_uno_el_segundo_espera_y_arranca_al_liberarse_el_cupo` | No |
| AC-07 (espera sin workers) | `tests/test_integracion_cliente_servidor.py::test_analisis_completo_con_redis_y_worker_reales` (worker arranca antes del deadline) | Sí |
| AC-08 (worker remoto sin el log) | `doc/VERIFICACION_DISTRIBUCION.md` (contenedor separado), `INSTALL.md` §Despliegue con Compose (worker real en su propio contenedor, `find` sin logs del proyecto) | Sí |
| AC-09 (recuperación sin duplicar) | `doc/VERIFICACION_DISTRIBUCION.md` (vía `analisis` directo), `doc/VERIFICACION_FALLOS.md` (vía coordinador/cliente real completo) | Sí |
| AC-10 (fallos finitos, Redis/MinIO/tarea) | `tests/test_fallos.py` (las cuatro pruebas) | Sí |
| AC-11 (aislamiento de timeout) | `tests/test_coordinador.py::test_timeout_de_un_job_no_cancela_al_otro_y_libera_su_cupo` (dobles) + `tests/test_fallos.py::test_timeout_real_no_cancela_un_job_concurrente_y_libera_cupo` (real) | Sí (la segunda) |
| AC-12 (histórico transaccional) | `tests/test_indexador.py`, `tests/test_coordinador_persistencia.py` | No (SQLite real, no hay servicio externo que simular) |
| AC-13 (señales y cierre) | `tests/test_operacion.py` (señales reales por `os.kill`) | Parcial (proceso real, sin Redis/MinIO) |
| AC-14 (CLI, IPv4/IPv6, `--raw`) | `tests/test_cliente.py::test_cliente_real_funciona_por_ipv6`, `::test_cliente_real_raw_stdout_es_exactamente_un_json_mas_lf`, `tests/test_integracion_cliente_servidor.py` | Sí |
| AC-15 (framing y rechazo de pedidos malformados) | `tests/test_protocolo.py` | No (nivel de protocolo, sin servicios externos) |
| AC-16 (generación reproducible) | `tests/test_generar.py`, `tests/test_integracion_nucleo.py` | No |
| AC-17 (despliegue y persistencia al recrear) | `INSTALL.md` §Despliegue con Docker Compose (checkout limpio real, ver issue #24) | Sí |
| AC-18 (benchmark 1/2/4 procesos) | Issue #26, fuera de alcance de este issue | — |
| AC-19 (desconexión limpia el job) | `tests/test_coordinador.py::test_desconexion_mientras_espera_cupo_lo_libera_sin_publicar_tareas`, `::test_desconexion_durante_la_ejecucion_revoca_el_grupo_y_libera_el_cupo` | No |
| AC-20 (indexador caído bloquea admisión) | `tests/test_operacion.py::test_supervisor_detecta_caida_real_del_indexador_y_bloquea_admision` (proceso hijo real), `tests/test_coordinador_persistencia.py` | Parcial |

CU-01 a CU-24 mapean a estos mismos AC en la tabla de `doc/USE_CASES.md`
§7 "Trazabilidad de aceptación"; no se repite acá.

## Qué se corrió para esta verificación, y contra qué configuración

Dos corridas reales, documentadas por separado porque ejercitan
configuraciones distintas a propósito:

### 1. Contenedores de desarrollo (`loghunter-redis`/`loghunter-minio` manuales) + un worker real

```bash
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked celery -A loghunter.tasks worker --loglevel=WARNING -c 2 -n fullrun@%h &
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin AWS_DEFAULT_REGION=us-east-1 \
uv run --locked python -m pytest -q
```

Resultado real (2026-11-10): **216 passed** (sin `skipped`, sin dobles donde
hay infraestructura real disponible) en 111.7s. Es la suite completa del
repositorio corriendo de punta a punta con Redis, MinIO y un worker Celery
reales simultáneamente, incluyendo `tests/test_fallos.py` deteniendo y
restaurando esos mismos contenedores durante la corrida sin dejarlos
degradados al terminar.

### 2. El despliegue Compose real (`docker-compose.yml`, no una configuración ad hoc)

Para que "la configuración real de Compose es la usada en la demo" sea
literal y no una afirmación sin evidencia:

```bash
docker compose up -d --scale worker=2
docker compose ps   # los cuatro servicios "healthy"
```

Con esa pila (no los contenedores manuales) arriba:

- `tests/test_integracion_distribucion.py -k "s3 or vacio"` (backend `s3://`,
  que es el que importa en modo distribuido): **3 passed**. Las variantes
  `local://` de ese mismo archivo se dejaron fuera a propósito: están
  parametrizadas asumiendo que *todos* los workers conectados comparten el
  filesystem del log (ver el docstring del archivo), y los workers de
  Compose están deliberadamente aislados del log del host (issue #24,
  AC-08) — correr esas variantes contra Compose falla con
  `FileNotFoundError` en el worker, y es el comportamiento **correcto**, no
  un defecto: confirma de nuevo, ahora contra el despliegue real, que ningún
  worker de Compose puede resolver una ruta local del coordinador.
- Dos clientes CLI reales, lanzados concurrentemente contra el puerto 8888
  publicado por Compose, cada uno con su propio archivo `s3://` y consulta
  (`top-ips` / `status`): ambos terminaron con `type: result` y el `file`/
  `query`/`lineas` correctos y propios, sin mezcla (AC-05 contra la
  configuración real de despliegue, no solo contra dobles).
- `docker compose down -v` al terminar: sin contenedores, redes ni procesos
  `celery` huérfanos (confirmado con `pgrep -fl celery`).

## Qué no se automatizó, y por qué

- **AC-18 (benchmark)** es explícitamente el issue #26; no se adelanta acá
  para no duplicar trabajo ni comprometer su metodología antes de tiempo.
- **AC-13/AC-20 "infra real" parcial**: la caída de Redis/MinIO durante el
  cierre con señales o con el indexador caído no se combinó en una sola
  prueba (señales reales + Redis/MinIO reales + indexador caído a la vez);
  cada mecanismo se verificó real por separado (`test_operacion.py` con
  procesos/señales reales; `test_fallos.py` con Redis/MinIO reales) porque
  combinarlos todos en una sola prueba no agrega certeza sobre una
  interacción que PROJECT_STATEMENT no describe como un escenario propio, y
  sí agrega una prueba frágil y lenta por el número de condiciones de carrera
  a sincronizar.
- **Instalación desde checkout limpio real** (clonar en un directorio nuevo,
  sin ningún estado previo del entorno de desarrollo) no se ejecutó en este
  host porque el checkout de trabajo ya tiene `.venv`/`uv.lock` resueltos;
  `tests/test_imports.py` cubre la parte que sí es automatizable (el paquete
  instalado se importa sin arrancar servicios desde otro directorio). El
  build de Compose (`docker compose build`) sí parte de un contexto de build
  limpio (sin `.venv` ni `.git`, excluidos por `.dockerignore`) y sí se
  ejecutó real para la verificación del issue #24.

## Permisos y procesos al terminar

Ninguna prueba de esta verificación, ni las corridas manuales de esta
sección, dejaron procesos `celery`/`docker` huérfanos ni contenedores
degradados: se confirmó con `pgrep -fl celery` y `docker ps`/`docker compose
ps` después de cada corrida. Los contenedores de desarrollo
(`loghunter-redis`/`loghunter-minio`) quedaron en el mismo estado en que
estaban antes de empezar.

## Referencias

PROJECT_STATEMENT §15; USE_CASES.md completo salvo CU-21; AC-01 a AC-17,
AC-19, AC-20; ADR-0013. Issues de origen de cada mecanismo: #3 a #22 (ver
`doc/DEVELOPMENT_PLAN.md`); fallos reales en #23 (`VERIFICACION_FALLOS.md`);
despliegue en #24 (`INSTALL.md`).

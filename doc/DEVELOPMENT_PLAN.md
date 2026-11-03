# Plan de desarrollo de LogHunter

Fecha: 2026-11-03. Repositorio: [manudiiez/umcomp2_final](https://github.com/manudiiez/umcomp2_final).

Este plan organiza el desarrollo completo por etapas técnicas. **Los milestones
no representan semanas ni exigen un entregable funcional semanal.** En las reuniones
con el profesor se pueden mostrar cambios de código, pruebas, decisiones y tareas
parciales. La aplicación completa se verifica al final del recorrido. No hay
fechas de vencimiento fijadas; el avance depende de las tareas y sus dependencias.

El contrato funcional está en [PROJECT_STATEMENT.md](PROJECT_STATEMENT.md), los
escenarios en [USE_CASES.md](USE_CASES.md) y las decisiones en [adr/INDEX.md](adr/INDEX.md).
Los issues de GitHub registran el estado del trabajo; esta guía registra el orden
inicial. Una dependencia es un prerrequisito técnico; el número de issue por sí solo
no impide avanzar tareas independientes de la misma etapa.

## Etapas

| Milestone | Alcance | Issues | Rama de integración prevista |
|---|---|---|---|
| [01: base del proyecto y flujo GitHub](https://github.com/manudiiez/umcomp2_final/milestone/1) | Política GitHub, paquete y entorno | 2 | `milestone/1-base-proyecto` |
| [02: núcleo de análisis y lectura local](https://github.com/manudiiez/umcomp2_final/milestone/2) | Parser, consultas, generador local, mmap y rangos | 6 | `milestone/2-analisis-local` |
| [03: distribución y almacenamiento remoto](https://github.com/manudiiez/umcomp2_final/milestone/3) | Celery, Redis, MinIO y grupos distribuidos | 6 | `milestone/3-distribucion-remota` |
| [04: cliente-servidor y gestión de jobs](https://github.com/manudiiez/umcomp2_final/milestone/4) | TCP/JSON, CLI, asyncio y ciclo de jobs | 5 | `milestone/4-cliente-servidor-jobs` |
| [05: persistencia, IPC y operación](https://github.com/manudiiez/umcomp2_final/milestone/5) | SQLite, proceso hijo, métricas, señales y cierre | 3 | `milestone/5-persistencia-ipc-operacion` |
| [06: integración, pruebas y entrega final](https://github.com/manudiiez/umcomp2_final/milestone/6) | Fallos, Compose, integración, benchmark y documentación | 6 | `milestone/6-integracion-entrega-final` |

Las ramas de la tabla son nombres previstos, **no ramas creadas**. Se preparan al
iniciar la etapa, desde `develop` actualizado, conservando las integraciones previas.
Esta planificación creó milestones e issues; no publicó ramas ni PRs.

## Orden sugerido y dependencias

### 01: base del proyecto y flujo GitHub

| Paso | Issue | Dependencias |
|---|---|---|
| 1 | [#1: chore(github): integrar la política de issues, milestones y PRs](https://github.com/manudiiez/umcomp2_final/issues/1) | Ninguna |
| 2 | [#2: build(project): preparar el paquete Python y el entorno de desarrollo](https://github.com/manudiiez/umcomp2_final/issues/2) | [#1](https://github.com/manudiiez/umcomp2_final/issues/1) |

### 02: núcleo de análisis y lectura local

| Paso | Issue | Dependencias |
|---|---|---|
| 3 | [#3: feat(parser): interpretar y validar registros combined](https://github.com/manudiiez/umcomp2_final/issues/3) | [#2](https://github.com/manudiiez/umcomp2_final/issues/2) |
| 4 | [#4: feat(consultas): implementar agregadores y referencia secuencial](https://github.com/manudiiez/umcomp2_final/issues/4) | [#3](https://github.com/manudiiez/umcomp2_final/issues/3) |
| 5 | [#5: feat(generador): crear logs locales reproducibles](https://github.com/manudiiez/umcomp2_final/issues/5) | [#3](https://github.com/manudiiez/umcomp2_final/issues/3) |
| 6 | [#6: feat(lector): implementar lectura local por rangos con mmap](https://github.com/manudiiez/umcomp2_final/issues/6) | [#2](https://github.com/manudiiez/umcomp2_final/issues/2) |
| 7 | [#7: feat(workers): procesar tramos sin perder ni duplicar registros](https://github.com/manudiiez/umcomp2_final/issues/7) | [#3](https://github.com/manudiiez/umcomp2_final/issues/3), [#4](https://github.com/manudiiez/umcomp2_final/issues/4), [#6](https://github.com/manudiiez/umcomp2_final/issues/6) |
| 8 | [#8: test(analisis): verificar el núcleo local y los límites de registros](https://github.com/manudiiez/umcomp2_final/issues/8) | [#4](https://github.com/manudiiez/umcomp2_final/issues/4), [#5](https://github.com/manudiiez/umcomp2_final/issues/5), [#7](https://github.com/manudiiez/umcomp2_final/issues/7) |

### 03: distribución y almacenamiento remoto

| Paso | Issue | Dependencias |
|---|---|---|
| 9 | [#9: build(infra): preparar Redis y MinIO para desarrollo](https://github.com/manudiiez/umcomp2_final/issues/9) | [#8](https://github.com/manudiiez/umcomp2_final/issues/8) |
| 10 | [#10: feat(workers): distribuir map_chunk con Celery y Redis](https://github.com/manudiiez/umcomp2_final/issues/10) | [#7](https://github.com/manudiiez/umcomp2_final/issues/7), [#9](https://github.com/manudiiez/umcomp2_final/issues/9) |
| 11 | [#11: feat(lector): leer objetos S3 mediante GET Range](https://github.com/manudiiez/umcomp2_final/issues/11) | [#6](https://github.com/manudiiez/umcomp2_final/issues/6), [#9](https://github.com/manudiiez/umcomp2_final/issues/9) |
| 12 | [#12: feat(generador): subir logs reproducibles a MinIO](https://github.com/manudiiez/umcomp2_final/issues/12) | [#5](https://github.com/manudiiez/umcomp2_final/issues/5), [#11](https://github.com/manudiiez/umcomp2_final/issues/11) |
| 13 | [#13: feat(analisis): correlacionar grupos y reducir parciales distribuidos](https://github.com/manudiiez/umcomp2_final/issues/13) | [#4](https://github.com/manudiiez/umcomp2_final/issues/4), [#10](https://github.com/manudiiez/umcomp2_final/issues/10), [#11](https://github.com/manudiiez/umcomp2_final/issues/11) |
| 14 | [#14: test(distribucion): verificar equivalencia y lectura desde otro host](https://github.com/manudiiez/umcomp2_final/issues/14) | [#10](https://github.com/manudiiez/umcomp2_final/issues/10), [#11](https://github.com/manudiiez/umcomp2_final/issues/11), [#12](https://github.com/manudiiez/umcomp2_final/issues/12), [#13](https://github.com/manudiiez/umcomp2_final/issues/13) |

### 04: cliente-servidor y gestión de jobs

| Paso | Issue | Dependencias |
|---|---|---|
| 15 | [#15: feat(protocolo): implementar mensajes JSON delimitados por LF](https://github.com/manudiiez/umcomp2_final/issues/15) | [#2](https://github.com/manudiiez/umcomp2_final/issues/2), [#14](https://github.com/manudiiez/umcomp2_final/issues/14) |
| 16 | [#16: feat(coordinador): atender clientes con asyncio sin bloquear el loop](https://github.com/manudiiez/umcomp2_final/issues/16) | [#13](https://github.com/manudiiez/umcomp2_final/issues/13), [#15](https://github.com/manudiiez/umcomp2_final/issues/15) |
| 17 | [#17: feat(cliente): ofrecer consultas CLI con progreso y salida raw](https://github.com/manudiiez/umcomp2_final/issues/17) | [#15](https://github.com/manudiiez/umcomp2_final/issues/15), [#16](https://github.com/manudiiez/umcomp2_final/issues/16) |
| 18 | [#18: feat(jobs): controlar admisión, progreso, timeout y cancelación](https://github.com/manudiiez/umcomp2_final/issues/18) | [#16](https://github.com/manudiiez/umcomp2_final/issues/16), [#17](https://github.com/manudiiez/umcomp2_final/issues/17) |
| 19 | [#19: test(jobs): verificar concurrencia y ciclo de vida de pedidos](https://github.com/manudiiez/umcomp2_final/issues/19) | [#17](https://github.com/manudiiez/umcomp2_final/issues/17), [#18](https://github.com/manudiiez/umcomp2_final/issues/18) |

### 05: persistencia, IPC y operación

| Paso | Issue | Dependencias |
|---|---|---|
| 20 | [#20: feat(indexador): persistir informes completos en SQLite](https://github.com/manudiiez/umcomp2_final/issues/20) | [#4](https://github.com/manudiiez/umcomp2_final/issues/4), [#16](https://github.com/manudiiez/umcomp2_final/issues/16) |
| 21 | [#21: feat(ipc): compartir métricas y supervisar el indexador](https://github.com/manudiiez/umcomp2_final/issues/21) | [#18](https://github.com/manudiiez/umcomp2_final/issues/18), [#20](https://github.com/manudiiez/umcomp2_final/issues/20) |
| 22 | [#22: feat(operacion): implementar señales y apagado con drenaje](https://github.com/manudiiez/umcomp2_final/issues/22) | [#18](https://github.com/manudiiez/umcomp2_final/issues/18), [#21](https://github.com/manudiiez/umcomp2_final/issues/21) |

### 06: integración, pruebas y entrega final

| Paso | Issue | Dependencias |
|---|---|---|
| 23 | [#23: test(fallos): validar recuperación y errores aislados](https://github.com/manudiiez/umcomp2_final/issues/23) | [#10](https://github.com/manudiiez/umcomp2_final/issues/10), [#18](https://github.com/manudiiez/umcomp2_final/issues/18), [#20](https://github.com/manudiiez/umcomp2_final/issues/20), [#21](https://github.com/manudiiez/umcomp2_final/issues/21), [#19](https://github.com/manudiiez/umcomp2_final/issues/19) |
| 24 | [#24: build(docker): desplegar servicios y workers con Compose](https://github.com/manudiiez/umcomp2_final/issues/24) | [#9](https://github.com/manudiiez/umcomp2_final/issues/9), [#12](https://github.com/manudiiez/umcomp2_final/issues/12), [#17](https://github.com/manudiiez/umcomp2_final/issues/17), [#20](https://github.com/manudiiez/umcomp2_final/issues/20), [#22](https://github.com/manudiiez/umcomp2_final/issues/22) |
| 25 | [#25: test(integracion): verificar contratos y despliegue completo](https://github.com/manudiiez/umcomp2_final/issues/25) | [#19](https://github.com/manudiiez/umcomp2_final/issues/19), [#22](https://github.com/manudiiez/umcomp2_final/issues/22), [#23](https://github.com/manudiiez/umcomp2_final/issues/23), [#24](https://github.com/manudiiez/umcomp2_final/issues/24) |
| 26 | [#26: perf(benchmark): medir análisis con 1, 2 y 4 procesos](https://github.com/manudiiez/umcomp2_final/issues/26) | [#14](https://github.com/manudiiez/umcomp2_final/issues/14), [#24](https://github.com/manudiiez/umcomp2_final/issues/24), [#25](https://github.com/manudiiez/umcomp2_final/issues/25) |
| 27 | [#27: docs(entrega): completar instalación, diseño y guía de uso](https://github.com/manudiiez/umcomp2_final/issues/27) | [#24](https://github.com/manudiiez/umcomp2_final/issues/24), [#25](https://github.com/manudiiez/umcomp2_final/issues/25), [#26](https://github.com/manudiiez/umcomp2_final/issues/26) |
| 28 | [#28: test(aceptacion): verificar el proyecto completo y preparar la revisión final](https://github.com/manudiiez/umcomp2_final/issues/28) | [#22](https://github.com/manudiiez/umcomp2_final/issues/22), [#23](https://github.com/manudiiez/umcomp2_final/issues/23), [#25](https://github.com/manudiiez/umcomp2_final/issues/25), [#26](https://github.com/manudiiez/umcomp2_final/issues/26), [#27](https://github.com/manudiiez/umcomp2_final/issues/27) |

## Cómo comenzar y seguir el trabajo

1. Comenzar por [#1](https://github.com/manudiiez/umcomp2_final/issues/1): la skill y las instrucciones ya están
   preparadas localmente; falta revisarlas y versionarlas por la ruta del issue.
   No convertir ese trabajo ya realizado en una implementación duplicada.
2. Preparar la rama del milestone cuando corresponda y esté autorizada su
   publicación. Para cada tarea, usar `<tipo>/<numero-issue>-<slug>` desde esa base.
3. Leer criterios y dependencias antes de implementar. Completar cambios pequeños,
   realizar verificaciones reales y registrar los límites de lo probado.
4. Usar Conventional Commits en castellano con `Refs #N`. Abrir el PR del issue
   a su rama milestone cuando se solicite publicar; el usuario revisa y mergea.
5. Promover por PR de milestone a `develop` para pruebas apropiadas al avance,
   y luego de `develop` a `main`. No hacer commits de trabajo ni push directo
   a ramas de integración. No se exige que cada etapa sea una aplicación completa.
6. Mantener issues y criterios actualizados. Abrir un PR no completa por sí solo
   una tarea; verificar integración y aceptación antes de darla por terminada.

Reglas completas: [skill flujo-github](../.claude/skills/flujo-github/SKILL.md) y
[ADR-0014](adr/0014-flujo-github-por-milestones.md). En GitHub, el cierre automático
depende de la rama predeterminada: los PRs intermedios usan `Refs`; no asumir que
integrar en una rama milestone cierra el issue.

## Seguimiento semanal con el profesor

Mostrar qué issues se trabajaron, commits/diffs relevantes, pruebas o resultados
parciales, problemas encontrados y próximos pasos. Un componente en desarrollo o
una prueba que reproduce un fallo también es evidencia de avance: no es necesario
preparar una demo completa cada semana. La verificación integral corresponde a
[#28](https://github.com/manudiiez/umcomp2_final/issues/28) y usa los 24 casos de uso y 20 criterios de aceptación.

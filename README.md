# LogHunter

Analizador distribuido de logs mediante map-reduce.

Trabajo final de **Computación II** — Universidad de Mendoza, Sede San Rafael.
Facultad de Ingeniería, Ingeniería en Informática.

## Estado

El núcleo de análisis local (parser, consultas, generador, lector `mmap` y
map-reduce por tramos) y la distribución remota ya están implementados y
verificados: tareas `map_chunk` distribuidas con Celery/Redis, lectura y
subida de logs a MinIO (`s3://`) por `GET Range`, correlación de grupos de
tareas y reduce de parciales, con equivalencia comprobada entre backend
local y S3 y recuperación tras perder un worker. La capa cliente-servidor
(`asyncio`, protocolo TCP) y la persistencia todavía están pendientes; ver
el detalle en [VERIFICACION_NUCLEO.md](doc/VERIFICACION_NUCLEO.md) y
[VERIFICACION_DISTRIBUCION.md](doc/VERIFICACION_DISTRIBUCION.md).
La especificación se encuentra
en [PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md), acompañada por los casos de uso y ADR.

## Documentación

| Archivo | Contenido |
|---|---|
| [INSTALL.md](INSTALL.md) | Preparación de `.venv`, instalación y ejecución de pruebas |
| [PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md) | Objetivo, alcance, tecnologías, arquitectura, contratos, ejemplos y aceptación |
| [USE_CASES.md](doc/USE_CASES.md) | Casos de uso con flujos, alternativas y resultados esperados |
| [Plan de desarrollo](doc/DEVELOPMENT_PLAN.md) | Etapas técnicas, issues de GitHub y dependencias para avanzar paso a paso |
| [Resumen del milestone 2](doc/milestone/RESUMEN_MILESTONE_2.md) | Qué se hizo y en qué archivo, por módulo del núcleo local |
| [Resumen del milestone 3](doc/milestone/RESUMEN_MILESTONE_3.md) | Qué se hizo y en qué archivo, por módulo de la distribución remota |
| [Verificación del núcleo](doc/VERIFICACION_NUCLEO.md) | Comandos y resultados reales de la verificación local (milestone 2) |
| [Verificación de la distribución](doc/VERIFICACION_DISTRIBUCION.md) | Comandos y resultados reales de la verificación remota (milestone 3) |
| [Índice de ADR](doc/adr/INDEX.md) | Decisiones de arquitectura y desarrollo, alternativas y consecuencias |
| [Skill de GitHub](.claude/skills/flujo-github/SKILL.md) | Conventional Commits y PRs de issue a milestone, develop y main |

## Desarrollo local

Con Python 3.12 y uv instalados:

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest
```

`uv` crea `.venv/` dentro del checkout y utiliza `uv.lock` para reproducir las
dependencias. La guía completa está en [INSTALL.md](INSTALL.md).

Durante el desarrollo se ampliará la instalación con el despliegue y se completarán
`INFO.md` (informe de diseño), `TODO.md` (mejoras futuras) y los ejemplos de uso.

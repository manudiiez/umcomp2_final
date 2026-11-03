# LogHunter

Analizador distribuido de logs mediante map-reduce.

Trabajo final de **Computación II** — Universidad de Mendoza, Sede San Rafael.
Facultad de Ingeniería, Ingeniería en Informática.

## Estado

El desarrollo comenzó con el paquete instalable, el entorno virtual y las pruebas
de importación. Las funcionalidades de análisis todavía están en desarrollo.
La especificación se encuentra
en [PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md), acompañada por los casos de uso y ADR.

## Documentación

| Archivo | Contenido |
|---|---|
| [INSTALL.md](INSTALL.md) | Preparación de `.venv`, instalación y ejecución de pruebas |
| [PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md) | Objetivo, alcance, tecnologías, arquitectura, contratos, ejemplos y aceptación |
| [USE_CASES.md](doc/USE_CASES.md) | Casos de uso con flujos, alternativas y resultados esperados |
| [Plan de desarrollo](doc/DEVELOPMENT_PLAN.md) | Etapas técnicas, issues de GitHub y dependencias para avanzar paso a paso |
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

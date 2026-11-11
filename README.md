# LogHunter

Analizador distribuido de logs mediante map-reduce.

Trabajo final de **Computación II** — Universidad de Mendoza, Sede San Rafael.
Facultad de Ingeniería, Ingeniería en Informática.

## Estado

Los seis milestones de [DEVELOPMENT_PLAN.md](doc/DEVELOPMENT_PLAN.md) están
implementados y verificados con infraestructura real, no solo con dobles de
prueba: núcleo de análisis local (parser, consultas, generador, lector
`mmap` por rangos), distribución remota con Celery/Redis y lectura/escritura
en MinIO (`s3://`), un coordinador `asyncio` con protocolo TCP/JSON que
atiende múltiples clientes sin bloquear su *event loop*, persistencia en
SQLite a través de un proceso indexador, IPC y señales para operación, y
despliegue completo con Docker Compose. Evidencia real de cada etapa en
los `doc/VERIFICACION_*.md` listados abajo. Pendiente: la promoción de
`develop` a `main` y la revisión final (issue #28).

## Uso rápido

Con Redis, MinIO, un worker Celery y el coordinador arriba (ver
[INSTALL.md](INSTALL.md) para el detalle completo o
[docker-compose.yml](docker-compose.yml) para levantar todo de una vez):

```bash
uv run --locked python -m loghunter.generar -o s3://logs/demo.log --size-mb 10 --seed 1
uv run --locked python -m loghunter.cliente -i 127.0.0.1 -p 8888 \
  --file s3://logs/demo.log --query top-ips --top 10
```

Consultas disponibles: `top-ips`, `status`, `urls`, `traffic`. Un recorrido
guiado y comentado, con qué mostrar y por qué, está en
[GUIA_DEMO.md](doc/GUIA_DEMO.md).

## Documentación

| Archivo | Contenido |
|---|---|
| [INSTALL.md](INSTALL.md) | Entorno, dependencias, servicios externos, despliegue con Compose y workers remotos |
| [INFO.md](INFO.md) | Decisiones de diseño y su justificación, enlazando los ADR |
| [TODO.md](TODO.md) | Mejoras futuras, a partir del alcance excluido y de límites reconocidos en la verificación |
| [PROJECT_STATEMENT.md](doc/PROJECT_STATEMENT.md) | Objetivo, alcance, tecnologías, arquitectura, contratos, ejemplos y criterios de aceptación |
| [USE_CASES.md](doc/USE_CASES.md) | 24 casos de uso con flujos, alternativas y resultados esperados |
| [DEVELOPMENT_PLAN.md](doc/DEVELOPMENT_PLAN.md) | Etapas técnicas, issues de GitHub y dependencias |
| [Índice de ADR](doc/adr/INDEX.md) | 15 decisiones de arquitectura y desarrollo, alternativas y consecuencias |
| [GUIA_DEMO.md](doc/GUIA_DEMO.md) | Recorrido ejecutable de punta a punta para mostrar el sistema en vivo |
| [OPERACION.md](doc/OPERACION.md) | Referencia de consultas, defaults de CLI, escalado de workers y dónde ver cada resultado |
| [VERIFICACION_NUCLEO.md](doc/VERIFICACION_NUCLEO.md) | Núcleo de análisis local (milestone 2) |
| [VERIFICACION_DISTRIBUCION.md](doc/VERIFICACION_DISTRIBUCION.md) | Distribución remota con Celery/Redis/MinIO (milestone 3) |
| [VERIFICACION_FALLOS.md](doc/VERIFICACION_FALLOS.md) | Recuperación y errores aislados con infraestructura real (milestone 6) |
| [VERIFICACION_INTEGRACION.md](doc/VERIFICACION_INTEGRACION.md) | Matriz de evidencia de los 20 criterios de aceptación (milestone 6) |
| [VERIFICACION_BENCHMARK.md](doc/VERIFICACION_BENCHMARK.md) | Medición real con 1, 2 y 4 procesos (milestone 6) |
| [Skill de GitHub](.claude/skills/flujo-github/SKILL.md) | Conventional Commits y PRs de issue a milestone, develop y main |

## Desarrollo local

Con Python 3.12 y uv instalados:

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest
```

`uv` crea `.venv/` dentro del checkout y utiliza `uv.lock` para reproducir las
dependencias. La guía completa, incluido el despliegue con Docker Compose,
está en [INSTALL.md](INSTALL.md).

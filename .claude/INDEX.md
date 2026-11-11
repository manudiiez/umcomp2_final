# Configuración de Claude Code

Scaffolding del proyecto. Todo lo de este directorio es **versionado y compartido**,
excepto `settings.local.json`, que es personal y está en `.gitignore`.

| Ruta | Para qué |
|---|---|
| `settings.json` | Configuración compartida del proyecto: permisos, variables de entorno, hooks |
| `settings.local.json` | Configuración personal, no se commitea |
| `skills/` | Skills del proyecto: instrucciones para tareas recurrentes |
| `agents/` | Subagentes especializados |
| `commands/` | Slash commands propios del proyecto |

El `CLAUDE.md` de la raíz es el contexto que se carga en cada sesión: arquitectura,
invariantes de diseño y convenciones del repositorio.

## Cómo agregar cosas

- **Skill**: crear `skills/<nombre>/SKILL.md` con frontmatter `name` y
  `description`. Se invoca con `/<nombre>` o la usa Claude cuando la descripción
  matchea la tarea.
- **Subagente**: crear `agents/<nombre>.md` con frontmatter `name`, `description`,
  y opcionalmente `model` y `tools`.
- **Slash command**: crear `commands/<nombre>.md` con el prompt a ejecutar.
- **Permisos**: en vez de editarlos a mano, conviene correr
  `/fewer-permission-prompts`, que analiza qué comandos se repiten y propone el
  allowlist.

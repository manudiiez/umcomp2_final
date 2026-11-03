# Instrucciones para agentes de LogHunter

Leer `doc/PROJECT_STATEMENT.md`, `doc/USE_CASES.md` y `doc/adr/INDEX.md` para
desarrollar el proyecto. Antes de gestionar cambios, commits o GitHub, leer y
aplicar [.claude/skills/flujo-github/SKILL.md](.claude/skills/flujo-github/SKILL.md).

- Conventional Commits obligatorios, con descripción en castellano.
- Todo trabajo versionado tiene un issue real asignado a un milestone.
- Ramas de issue desde su rama milestone; integración exclusivamente por PR:
  `rama del issue → rama del milestone → develop → main`.
- Nunca commitear cambios ni hacer push directo a `main`, `develop` o ramas
  milestone existentes. El usuario revisa y mergea los PRs.
- No crear issues, milestones, ramas remotas o PRs sin autorización en la tarea.
  Cuando ya está dada, proceder dentro de su alcance sin volver a solicitarla.
- Preservar cambios existentes. No hacer commits en una rama protegida si todavía
  falta crear el issue/milestone y trasladar el trabajo a su rama.

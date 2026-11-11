# Conventional Commits de LogHunter

Formato obligatorio: `tipo(scope opcional)!: descripción`.
Usar descripción en español, imperativo y sin punto final. Como convención del
proyecto, procurar una primera línea de hasta 72 caracteres y agregar el detalle
necesario en el cuerpo. Cada cambio coherente va en su propio commit.

Tipos: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`,
`style`, `revert`. Scopes: los módulos de LogHunter; omitirlo si es transversal.
Las promociones también llevan título convencional. No usar mensajes automáticos
no convencionales como excepción al contrato; preparar el mensaje del merge para
que el usuario pueda conservar el formato al integrar.

```text
feat(parser): interpretar registros combined

Validar IP, fecha y bytes; contabilizar registros inválidos sin abortar.

Refs #12
```

Otros ejemplos: `fix(lector): conservar líneas en cortes exactos`,
`test(workers): verificar recuperación sin duplicar conteos`,
`chore(integration): integrar entrega 2 en develop`.

Marcar cambios incompatibles con `!` o un pie `BREAKING CHANGE:` que explique
el cambio y la migración. No generar versiones o tags a partir de los commits
si el usuario no lo pidió. No confundir Conventional Commits con autorización
para publicar cambios o cerrar issues.

Los IDs de ejemplo no representan issues existentes. Los commits intermedios y
PRs a ramas milestone usan `Refs #N`. El cierre se resuelve con el flujo descrito
en [SKILL.md](../SKILL.md), según la rama predeterminada real del repo.

Fuente: [Conventional Commits 1.0.0](https://www.conventionalcommits.org/es/v1.0.0/).

# ADR-0014 — Issues con milestone e integración por pull requests

- Fecha: 2026-11-03.
- Estado: adoptado por instrucción del usuario; sustituye la elección de trabajar
  directamente en `main`, manteniendo la exigencia de desarrollo incremental de ADR-0013.

## Contexto

El desarrollo se organiza en etapas técnicas con avances que se muestran semana
a semana. No se exige un entregable funcional semanal. Se necesita trazabilidad
entre tareas, cambios y etapas, y probar cada integración antes de llevarla a main.

## Decisión

Usar Conventional Commits en todos los commits y títulos de PR. Cada issue tiene
un milestone, una rama `<tipo>/<numero-issue>-<slug>` y un PR a la rama
`milestone/<numero-milestone>-<slug>` correspondiente. Las ramas de issue nacen
de su rama milestone; una rama milestone nueva parte de `develop`.
Si develop remota todavía no existe, inicializarla únicamente en el commit ya
publicado de main; esa creación de referencia no publica cambios de trabajo.
Los commits locales aún no publicados se conservan y los archivos necesarios
se incorporan mediante la rama y el PR del issue, sin adelantar develop por fuera de PR.

Integrar exclusivamente por PR: issue → milestone → `develop` → `main`.
No hacer commits de trabajo ni push directo a ramas de integración; no eludirlo
con actualizaciones de refs por API. El usuario revisa y mergea los PRs.
La skill [.claude/skills/flujo-github/SKILL.md](../../.claude/skills/flujo-github/SKILL.md)
detalla nombres, verificación previa, permisos y promociones.

## Alternativas

- Desarrollo directo en main: no permite la revisión y prueba previas requeridas.
- Issues sin milestone: pierden la relación con la entrega semanal.
- PR de cada issue a develop: evita la rama de integración de la entrega.
- Rama testing adicional o hotfix directo a main: introduce rutas no solicitadas.

## Consecuencias

Hay que preparar milestone e issue antes de versionar una tarea. Las promociones
no suponen ejecutar automáticamente merges ni configurar protecciones del repo.
Los issues intermedios se referencian con `Refs`; el cierre automático se verifica
según la rama predeterminada real, sin asumir que ocurre al integrar un milestone.

## Verificación y referencias

Comprobar issue/milestone, base de cada rama, destino de cada PR y formato de
commits. Confirmar integración y pruebas antes de promover a main.
Referencias: PROJECT_STATEMENT §16, ADR-0013 y la skill del proyecto.

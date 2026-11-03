---
name: flujo-github
description: Gestionar el trabajo Git y GitHub de LogHunter mediante Conventional Commits, issues con milestone, ramas por issue y promociones por PR. Usar al preparar cambios, commits, issues, milestones, ramas o pull requests de manudiiez/umcomp2_final.
---

# Flujo GitHub de LogHunter

Repositorio: `manudiiez/umcomp2_final`. Aplicar a cambios de código, documentación,
pruebas y configuración. Requisitos: `doc/PROJECT_STATEMENT.md` y `doc/USE_CASES.md`.

## Reglas obligatorias

1. Todo trabajo versionado corresponde a un **issue real con milestone real**.
   No inventar números ni dejar issues nuevos sin milestone.
2. Todo commit y título de PR usa **Conventional Commits**, con descripción en
   español, imperativo y sin punto final. Incluir `Refs #N` en commits de trabajo.
3. Nunca hacer commits de trabajo ni push directo a `main`, `develop` o ramas
   `milestone/*`. No eludirlo mediante API de refs, force push o quitando protecciones.
4. Integrar exclusivamente por PR:

   ```text
   <tipo>/<issue>-<slug> → milestone/<numero>-<slug> → develop → main
                         PR                       PR        PR
   ```

5. Las ramas de issue nacen de **su rama milestone**, no de develop/main.
   `develop` es donde se prueba la integración. No hay atajos issue → develop/main
   ni milestone → main, tampoco para fixes.
6. El agente prepara PRs; **el usuario revisa y mergea**. No ejecutar merges,
   crear tags/releases ni cambiar la configuración remota por iniciativa propia.
7. Preservar cambios existentes: no hacer reset/clean, stash automático ni
   sobrescribir trabajo ajeno para obtener un árbol limpio.

La skill no concede autorización remota por sí sola: usar el alcance del pedido
activo. Si ya se autorizó una acción, no volver a pedirla. Si falta autorización,
preparar el resultado concreto antes de solicitar la aprobación necesaria.
Un pedido de planificar/crear issues no autoriza pushes, PRs ni ramas remotas.

## Convenciones

| Elemento | Formato / ejemplo |
|---|---|
| Milestone | `Entrega 1: análisis local`; número real asignado por GitHub |
| Rama de milestone | `milestone/1-analisis-local` |
| Rama de issue | `feat/12-parser-combined`, `fix/18-cortes-lf`, `docs/7-flujo-github` |
| Commit | `feat(parser): interpretar registros combined` con pie `Refs #12` |
| PR de issue | Título convencional; cuerpo con issue, milestone, alcance y pruebas |
| PR milestone → develop | `chore(integration): integrar entrega 1 en develop` |
| PR develop → main | `chore(release): promover develop a main` |

Los números anteriores son ilustrativos. El número de milestone no es un issue.
El plan real de etapas e issues está en `doc/DEVELOPMENT_PLAN.md`.
Scopes recomendados: `cliente`, `coordinador`, `lector`, `parser`, `consultas`,
`workers`, `indexador`, `generador`, `protocolo`, `config`, `db`, `docker`, `github`,
`docs`. Omitir scope para cambios transversales. Detalle en
[conventional-commits.md](references/conventional-commits.md).

## Preparar trabajo

- Revisar status, rama y remotos; confirmar el repo. Usar `--repo
  manudiiez/umcomp2_final` en operaciones gh. Consultar issue, comentarios,
  milestone, dependencias y PRs existentes; no duplicarlos.
- Hacer fetch cuando se necesite una base remota reciente; no para cada lectura local.
- Si faltan issue/milestone y su creación está autorizada, crear primero el milestone
  y después el issue asignado. Si no está autorizada, preparar propuesta y pedir lo
  mínimo faltante. No crear una rama sin número.
- Si se está en main/develop, conservar cambios sin commit hasta disponer del issue
  y trasladarlos a su rama sin pérdida. No commitear primero para ordenar después.

## Planificar milestones e issues

Organizar el proyecto en etapas técnicas que cubran todo su alcance. Los avances
se muestran semana a semana, sin exigir una aplicación funcional ni una demo
completa al terminar cada semana. Los milestones no equivalen a semanas ni tienen
fechas de vencimiento inventadas.
Cada milestone define objetivo, alcance y condición de finalización. Cada issue tiene
objetivo acotado, tareas, criterios verificables, dependencias por número real,
referencias a especificación/casos y milestone. No agregar funcionalidades excluidas.
No crear etiquetas/asignaciones arbitrarias ni describir como pendiente algo ya hecho.

Antes de crear, listar issues y milestones existentes. Usar archivos temporales con
`--body-file` o argumentos estructurados para textos multilínea. Tras un fallo de
red, consultar si el objeto se creó antes de repetir. Si persiste un error de
permisos/autenticación, informar el bloqueo sin ampliar permisos ni repetir escrituras
indefinidamente.

## Resolver un issue

1. Verificar milestone y dependencias. Identificar `milestone/<numero>-<slug>`.
   Si `origin/develop` aún no existe en el repositorio inicial, se puede crear
   únicamente como referencia nueva al commit ya publicado de `origin/main`, con
   autorización para preparar ramas. No publicar ahí commits locales adelantados
   ni actualizar una rama develop existente por API o push.
   Si falta la rama milestone, crearla desde `origin/develop` con esa autorización.
   Publicar inicialmente la referencia al commit existente de base no equivale a
   ingresar cambios: no agregar commits ni actualizar una rama milestone existente.
2. Crear `<tipo>/<N>-<slug>` desde la rama milestone actualizada.
   Si el trabajo local parte de otra base o tiene documentación aún no publicada,
   conservarlo y usar un worktree aislado. Llevar los archivos necesarios al PR
   como contexto explícito, sin sobrescribir el checkout original ni adelantar
   ramas de integración para ocultar la diferencia.
3. Implementar el alcance del issue, ejecutar sus verificaciones y hacer commits
   incrementales convencionales. Revisar el diff antes de agregar archivos.
4. Cuando se haya pedido publicar, verificar origen/destino otra vez y hacer push
   explícito solo de la rama del issue; abrir su PR a la rama milestone.
5. Incluir `Refs #N`, milestone, problema, comportamiento y validación real en el PR.
   No presentar pruebas no ejecutadas como aprobadas; usar borrador si está incompleto.
6. Entregar el enlace; no mergear ni cerrar el issue por haber abierto el PR.

No reescribir la historia existente para convertir commits anteriores a estas
convenciones; aplicarlas al trabajo nuevo.

Ejemplo para IDs y ramas que ya existan:

```bash
git switch -c feat/12-parser-combined origin/milestone/1-analisis-local
git push --set-upstream origin feat/12-parser-combined
gh pr create --repo manudiiez/umcomp2_final \
  --base milestone/1-analisis-local --head feat/12-parser-combined \
  --title 'feat(parser): interpretar registros combined' --body-file /tmp/loghunter-pr.md
```

## Promover una entrega

Revisar diff, issues/PRs incluidos, conflictos, verificaciones y promociones ya
abiertas. Cada promoción es un PR; el usuario lo mergea.

| Origen | Destino | Condición |
|---|---|---|
| Rama de issue | Su rama milestone | Criterios del issue cubiertos y cambio revisable |
| Rama milestone | develop | Issues integrados por PR y avance de la etapa listo para validar |
| develop | main | Integración y aceptación probadas sobre el contenido promovido |

No inventar checks CI ni exigir workflows que no existan. Registrar validación real.
Los arreglos encontrados en develop se realizan mediante issue del milestone y el
mismo circuito. Los conflictos se resuelven en una rama de issue que entre por PR;
no escribir directamente sobre destinos protegidos.

Para promociones entre ramas duraderas, preferir merge commit para conservar
ascendencia; usar título/mensaje convencional. No cambiar métodos del repo sin
pedido. Si no puede conservarse una historia convencional, preparar la promoción
sin mergearla y explicar la configuración necesaria.

Los PRs intermedios usan `Refs #N`. El cierre automático depende de la rama
predeterminada: no prometer que `Closes #N` cierra al integrar en un milestone.
El PR final a main puede usar `Closes #N` para issues terminados si main sigue
siendo la rama predeterminada. En otro caso, acordar cierre manual autorizado;
no cambiar default branch por iniciativa propia. Cerrar el milestone tras integrar
la entrega y validar sus criterios, no simplemente por haber creado los issues.

## Recursos y alcance

La skill define conducta; no implica que existan hooks/protecciones/checks instalados.
Para instalarlos cuando se solicite, leer [instalacion.md](references/instalacion.md).
Para diseñar validaciones, leer [ci.md](references/ci.md).

Los recursos preexistentes en `scripts/` y `templates/` implementan otro flujo
con testing, tags e imágenes. **No ejecutarlos ni instalarlos en este proyecto**.
No introducir testing, Dokploy, releases automáticas o atajos hotfix.

Fuentes: [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/) y
[vinculación de PRs e issues en GitHub](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).

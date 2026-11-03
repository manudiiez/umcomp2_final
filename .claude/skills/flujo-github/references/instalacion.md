# Preparar el flujo en GitHub

Leer solo si se pide preparar ramas, hooks, plantillas o protecciones reales.
La existencia de la skill no significa que esas medidas estén instaladas.

1. Inspeccionar el repo, default branch, permisos, ramas, reglas, workflows e
   issues/milestones existentes. No crear duplicados ni sobrescribir trabajo.
2. Conservar main/develop existentes. Si develop remota falta en el repositorio
   inicial, crear solo una referencia nueva al commit ya publicado de main. No
   publicar en develop commits locales adelantados. Si la rama existe, conservarla
   sin resetear ni actualizar sus refs fuera de PR. Un checkout local divergente
   se conserva y se usa un worktree aislado para preparar el cambio revisable.
3. Definir milestones y ramas `milestone/<numero>-<slug>`; cada una parte de develop.
   La primera publicación de una rama milestone apunta al commit existente de base;
   no agregar commits ni actualizar ramas milestone que ya existan por push directo.
4. Preparar cambios de hooks/CI/plantillas en la rama de un issue del milestone.
   Publicar el PR a esa rama milestone solo si está autorizado.
5. Si se solicitaron protecciones, configurar las reglas para main, develop y
   milestone/* y prohibir pushes directos. Verificar capacidades del plan y permisos
   antes de afirmar que quedaron activas. No cambiar default branch sin pedido.
6. Dejar constancia de lo configurado, lo no disponible y las pruebas realizadas.

La matriz de PRs es issue → su milestone → develop → main. No instalar testing,
excepciones hotfix, despliegues ni versiones automáticas que no se hayan pedido.
Los recursos existentes de `scripts/` y `templates/` no implementan esta política:
**no ejecutarlos/copiar sus configuraciones sin reemplazarlos y revisarlos primero**.

La autorización existente para una configuración concreta se respeta; no agregar
confirmaciones repetidas. Si ampliar la tarea requiere autorización, preparar el
cambio propuesto antes de pedirla. Crear issues no autoriza configurar el repo.

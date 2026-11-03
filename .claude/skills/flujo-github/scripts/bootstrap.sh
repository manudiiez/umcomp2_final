#!/usr/bin/env bash
# flujo-github — Fase 1: ramas develop/testing desde main, rama por defecto, métodos de merge y etiquetas.
# No modifica main. Usa la API de GitHub (gh), no el checkout local.
#
# Uso: bootstrap.sh [--dry-run] [--repo owner/nombre] [--main main] [--keep-default-branch]
set -euo pipefail

DRY=0; REPO=""; MAIN="main"; SET_DEFAULT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --repo) REPO="$2"; shift ;;
    --main) MAIN="$2"; shift ;;
    --keep-default-branch) SET_DEFAULT=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 64 ;;
  esac
  shift
done

command -v gh >/dev/null || { echo "Falta gh (GitHub CLI)." >&2; exit 1; }
[[ -n "$REPO" ]] || REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

say()  { printf '%s\n' "$*"; }
run()  { if (( DRY )); then say "  [dry-run] $*"; else "$@"; fi; }

say "Repositorio: $REPO  (rama base: $MAIN)$( (( DRY )) && echo '  — DRY RUN')"

MAIN_SHA="$(gh api "repos/$REPO/git/ref/heads/$MAIN" --jq .object.sha)" \
  || { echo "No existe la rama $MAIN en $REPO." >&2; exit 1; }
say "• $MAIN = ${MAIN_SHA:0:7} (no se modifica)"

blocked=0
ensure_branch() {
  local b="$1" cmp ahead behind
  if gh api "repos/$REPO/git/ref/heads/$b" >/dev/null 2>&1; then
    cmp="$(gh api "repos/$REPO/compare/$MAIN...$b" --jq '"\(.ahead_by) \(.behind_by)"')"
    read -r ahead behind <<<"$cmp"
    if (( ahead > 0 )); then
      say "✖ '$b' existe y tiene $ahead commit(s) que NO están en $MAIN. No se toca:"
      gh api "repos/$REPO/compare/$MAIN...$b" --jq '.commits[] | "    \(.sha[0:7]) \(.commit.message | split("\n")[0])"' | tail -n 20
      say "  Decidí con el usuario: mergearlos a $MAIN, renombrar '$b' a '$b-old', o usarla como está."
      blocked=1
    elif (( behind > 0 )); then
      say "• '$b' está $behind commit(s) detrás de $MAIN → fast-forward a $MAIN"
      run gh api -X PATCH "repos/$REPO/git/refs/heads/$b" -f sha="$MAIN_SHA" -F force=false >/dev/null
    else
      say "• '$b' ya existe y está igual que $MAIN"
    fi
  else
    say "• Creando '$b' desde $MAIN"
    run gh api -X POST "repos/$REPO/git/refs" -f ref="refs/heads/$b" -f sha="$MAIN_SHA" >/dev/null
  fi
}

ensure_branch develop
ensure_branch testing

if (( blocked )); then
  say ""
  say "Instalación detenida: resolver las ramas marcadas con ✖ y volver a ejecutar."
  exit 2
fi

say "• Configurando métodos de merge (merge commit + squash con título del PR, sin rebase; borrar ramas al mergear)"
run gh api -X PATCH "repos/$REPO" \
  -F allow_merge_commit=true -F allow_squash_merge=true -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY \
  -f merge_commit_title=PR_TITLE -f merge_commit_message=PR_BODY >/dev/null

if (( SET_DEFAULT )); then
  say "• Rama por defecto → develop"
  run gh api -X PATCH "repos/$REPO" -f default_branch=develop >/dev/null
fi

say "• Etiquetas"
label() { run gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force >/dev/null; }
label "bug"              "d73a4a" "Algo no funciona"
label "feature"          "0e8a16" "Funcionalidad nueva"
label "mejora"           "a2eeef" "Mejora de algo existente"
label "chore"            "c5def5" "Mantenimiento"
label "docs"             "0075ca" "Documentación"
label "hotfix"           "b60205" "Arreglo urgente sobre main"
label "prioridad:critica" "b60205" "Producción caída o datos en riesgo"
label "prioridad:alta"   "d93f0b" "Funcionalidad clave rota"
label "prioridad:media"  "fbca04" "Hay alternativa"
label "prioridad:baja"   "c2e0c6" "Menor"

say ""
say "✔ Fase 1 completa. Siguiente: rama chore/configurar-flujo-github + scripts/install-files.sh (ver references/instalacion.md)."

#!/usr/bin/env bash
# flujo-github — Fase 2: copia workflows, plantillas, hooks, permisos de Claude Code, bloque de CLAUDE.md
# y la propia skill al repo actual. Nunca pisa archivos existentes: deja <archivo>.flujo-nuevo al lado.
#
# Uso: install-files.sh [--dry-run]   (ejecutar desde dentro del repo, en la rama chore/configurar-flujo-github)
set -euo pipefail

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$SKILL_DIR/templates"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

say() { printf '%s\n' "$*"; }
created=(); pending=(); skipped=()

copy_safe() {  # $1 origen  $2 destino (relativo al repo)
  local src="$1" dst="$2"
  if [[ -e "$dst" ]]; then
    if cmp -s "$src" "$dst"; then skipped+=("$dst (idéntico)"); return; fi
    pending+=("$dst.flujo-nuevo")
    (( DRY )) || cp "$src" "$dst.flujo-nuevo"
  else
    created+=("$dst")
    (( DRY )) || { mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"; }
  fi
}

# GitHub
for f in "$T"/github/workflows/*.yml;      do copy_safe "$f" ".github/workflows/$(basename "$f")"; done
for f in "$T"/github/ISSUE_TEMPLATE/*.yml; do copy_safe "$f" ".github/ISSUE_TEMPLATE/$(basename "$f")"; done
copy_safe "$T/github/pull_request_template.md" ".github/pull_request_template.md"

# Hooks
for f in "$T"/githooks/*; do copy_safe "$f" ".githooks/$(basename "$f")"; done
(( DRY )) || chmod +x .githooks/* 2>/dev/null || true

# Permisos de Claude Code: fusionar deny sin perder lo existente
if [[ -f .claude/settings.json ]]; then
  if command -v jq >/dev/null; then
    if (( ! DRY )); then
      tmp="$(mktemp)"
      jq -s '.[0] as $a | .[1] as $b
             | $a * {permissions: (($a.permissions // {}) + {deny: ((($a.permissions.deny // []) + $b.permissions.deny) | unique)})}' \
         .claude/settings.json "$T/claude/settings.json" > "$tmp" && mv "$tmp" .claude/settings.json
    fi
    created+=(".claude/settings.json (deny fusionado)")
  else
    copy_safe "$T/claude/settings.json" ".claude/settings.json"
  fi
else
  copy_safe "$T/claude/settings.json" ".claude/settings.json"
fi

# CLAUDE.md: insertar bloque entre marcadores (idempotente)
if [[ -f CLAUDE.md ]] && grep -q 'flujo-github:inicio' CLAUDE.md; then
  skipped+=("CLAUDE.md (el bloque flujo-github ya existe)")
else
  if (( ! DRY )); then
    [[ -f CLAUDE.md ]] && printf '\n' >> CLAUDE.md
    cat "$T/claude/CLAUDE.md.snippet" >> CLAUDE.md
  fi
  created+=("CLAUDE.md (bloque flujo-github agregado)")
fi

# La skill dentro del repo
DEST_SKILL="$ROOT/.claude/skills/flujo-github"
if [[ "$(cd "$SKILL_DIR" && pwd -P)" != "$( [[ -d "$DEST_SKILL" ]] && cd "$DEST_SKILL" && pwd -P )" ]]; then
  if [[ -d "$DEST_SKILL" ]]; then
    skipped+=(".claude/skills/flujo-github (ya existe, no se pisa)")
  else
    created+=(".claude/skills/flujo-github/")
    (( DRY )) || { mkdir -p "$DEST_SKILL"; cp -R "$SKILL_DIR"/. "$DEST_SKILL"/; }
  fi
fi

# Activar hooks en este clon
(( DRY )) || git config core.hooksPath .githooks

say "Instalación de archivos$( (( DRY )) && echo ' (DRY RUN)'):"
for x in "${created[@]+"${created[@]}"}"; do say "  + $x"; done
for x in "${skipped[@]+"${skipped[@]}"}"; do say "  = $x"; done
for x in "${pending[@]+"${pending[@]}"}"; do say "  ! $x  ← ya existía; integrar a mano y borrar el .flujo-nuevo"; done
say "  core.hooksPath = .githooks"
say ""
say "Pendiente (manual): adaptar workflows (references/ci.md), neutralizar el workflow viejo,"
say "completar scopes y comandos en CLAUDE.md, commit + PR a develop."

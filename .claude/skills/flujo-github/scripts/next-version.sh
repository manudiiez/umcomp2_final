#!/usr/bin/env bash
# flujo-github — Sugiere la próxima versión SemVer leyendo los Conventional Commits desde el último tag.
# No crea nada: solo informa. Salida 3 si no hay cambios que justifiquen versión.
#
# Uso: next-version.sh [--ref origin/main]
set -euo pipefail

REF="origin/main"
[[ "${1:-}" == "--ref" ]] && REF="$2"

git rev-parse --verify --quiet "$REF" >/dev/null || { echo "No existe la referencia $REF (¿git fetch?)." >&2; exit 1; }

LAST="$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' "$REF" 2>/dev/null || true)"
if [[ -n "$LAST" ]]; then RANGE="$LAST..$REF"; BASE="${LAST#v}"; else RANGE="$REF"; BASE="0.0.0"; fi
IFS=. read -r MA MI PA <<<"$BASE"

level=0   # 0 nada, 1 patch, 2 minor, 3 major
changes=()
re_head='^([a-z]+)(\([^)]*\))?(!)?: (.+)$'

while IFS= read -r -d $'\x1e' entry; do
  while [[ "$entry" == $'\n'* ]]; do entry="${entry#$'\n'}"; done
  subject="${entry%%$'\n'*}"
  body="${entry#*$'\n'}"
  [[ -z "$subject" ]] && continue
  if [[ "$subject" =~ $re_head ]]; then
    type="${BASH_REMATCH[1]}"; bang="${BASH_REMATCH[3]}"
    l=0
    case "$type" in feat) l=2 ;; fix|perf) l=1 ;; esac
    if [[ -n "$bang" ]] || grep -qE '^BREAKING[ -]CHANGE:' <<<"$body"; then l=3; fi
    (( l > level )) && level=$l
    changes+=("$subject")
  else
    changes+=("$subject  (no convencional, ignorado para la versión)")
  fi
done < <(git log --no-merges --format='%s%n%b%x1e' "$RANGE")

# En 0.x un breaking change sube minor
if (( level == 3 && MA == 0 )); then level=2; fi

case $level in
  3) NEXT="$((MA+1)).0.0"; NAME="major" ;;
  2) NEXT="$MA.$((MI+1)).0"; NAME="minor" ;;
  1) NEXT="$MA.$MI.$((PA+1))"; NAME="patch" ;;
  0) NEXT=""; NAME="ninguno" ;;
esac

echo "Último tag:  ${LAST:-ninguno}"
echo "Referencia:  $REF ($(git rev-parse --short "$REF"))"
echo "Cambios:     ${#changes[@]}"
for c in "${changes[@]+"${changes[@]}"}"; do echo "  - $c"; done
if [[ -z "$NEXT" ]]; then
  echo "Sugerida:    — (solo hay cambios sin impacto en la versión: docs/chore/refactor/…)"
  exit 3
fi
echo "Nivel:       $NAME"
echo "Sugerida:    v$NEXT"

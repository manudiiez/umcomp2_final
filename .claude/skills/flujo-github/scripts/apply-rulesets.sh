#!/usr/bin/env bash
# flujo-github — Fase 3: rulesets que obligan a entrar por PR a develop/testing/main y protegen tags v*.
# Idempotente: actualiza por nombre si ya existen. Sin bypass (aplica también a admins).
#
# Uso: apply-rulesets.sh [--dry-run] [--repo owner/nombre] [--main main] [--approvals N] [--no-checks]
set -euo pipefail

DRY=0; REPO=""; MAIN="main"; APPROVALS=0; CHECKS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --repo) REPO="$2"; shift ;;
    --main) MAIN="$2"; shift ;;
    --approvals) APPROVALS="$2"; shift ;;
    --no-checks) CHECKS=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1" >&2; exit 64 ;;
  esac
  shift
done

command -v jq >/dev/null || { echo "Falta jq." >&2; exit 1; }
if [[ -z "$REPO" ]]; then
  if (( DRY )) && ! gh auth status >/dev/null 2>&1; then REPO="owner/repo"; else
    REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"; fi
fi

# Nombres de checks = `name:` de los jobs en .github/workflows/pr-checks.yml
REQUIRED_CHECKS='["pr-title","branch-policy"]'

branch_ruleset() {  # $1 nombre  $2 rama  $3 métodos de merge (JSON array)
  jq -n --arg name "$1" --arg ref "refs/heads/$2" --argjson methods "$3" \
        --argjson approvals "$APPROVALS" --argjson checks "$REQUIRED_CHECKS" --argjson with_checks "$CHECKS" '
  {
    name: $name, target: "branch", enforcement: "active", bypass_actors: [],
    conditions: { ref_name: { include: [$ref], exclude: [] } },
    rules: (
      [ {type: "deletion"}, {type: "non_fast_forward"},
        {type: "pull_request", parameters: {
            required_approving_review_count: $approvals,
            dismiss_stale_reviews_on_push: false,
            require_code_owner_review: false,
            require_last_push_approval: false,
            required_review_thread_resolution: false,
            allowed_merge_methods: $methods } } ]
      + (if $with_checks == 1 then
          [ {type: "required_status_checks", parameters: {
              strict_required_status_checks_policy: false,
              required_status_checks: ($checks | map({context: .})) } } ]
         else [] end)
    )
  }'
}

tag_ruleset() {
  jq -n '{
    name: "flujo: tags de version", target: "tag", enforcement: "active", bypass_actors: [],
    conditions: { ref_name: { include: ["refs/tags/v*"], exclude: [] } },
    rules: [ {type: "deletion"}, {type: "update"}, {type: "non_fast_forward"} ]
  }'
}

apply() {  # $1 json
  local json="$1" name id out
  name="$(jq -r .name <<<"$json")"
  if (( DRY )); then
    echo "── [dry-run] $name"; echo "$json"; return 0
  fi
  id="$(gh api "repos/$REPO/rulesets?includes_parents=false" --jq ".[] | select(.name == \"$name\") | .id" | head -n1)"
  if [[ -n "$id" ]]; then
    out="$(gh api -X PUT "repos/$REPO/rulesets/$id" --input - <<<"$json" 2>&1)" || { report_error "$name" "$out"; return 1; }
    echo "• Actualizado: $name"
  else
    out="$(gh api -X POST "repos/$REPO/rulesets" --input - <<<"$json" 2>&1)" || { report_error "$name" "$out"; return 1; }
    echo "• Creado: $name"
  fi
}

report_error() {
  echo "✖ No se pudo aplicar '$1':" >&2
  echo "$2" | sed 's/^/    /' >&2
  if grep -qiE '403|upgrade|not available|plan' <<<"$2"; then
    echo "  Probable causa: rulesets en repo privado requieren GitHub Pro (cuenta personal) o Team (organización)." >&2
    echo "  La protección queda solo local (hook pre-push + permisos de Claude Code)." >&2
    exit 3
  fi
}

echo "Repositorio: $REPO$( (( DRY )) && echo '  — DRY RUN')  aprobaciones=$APPROVALS checks=$CHECKS"
apply "$(branch_ruleset "flujo: develop" develop '["squash","merge"]')"
apply "$(branch_ruleset "flujo: testing" testing '["merge"]')"
apply "$(branch_ruleset "flujo: main"    "$MAIN"  '["merge"]')"
apply "$(tag_ruleset)"
echo "✔ Rulesets aplicados. Desde ahora develop/testing/$MAIN solo aceptan cambios por PR."

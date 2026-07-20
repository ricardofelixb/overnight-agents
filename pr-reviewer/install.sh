#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
CUSTOM_SKILL="$SCRIPT_DIR/skills/autonomous-pr-review"
SIMPLIFIER_SKILL="$SCRIPT_DIR/../pr-simplifier/skills/simplify-pr-implementation"
VALIDATOR="${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py"

if [[ ! -f "$VALIDATOR" ]]; then
  echo "Missing Codex skill validator: $VALIDATOR" >&2
  exit 1
fi

python3 "$VALIDATOR" "$CUSTOM_SKILL"
python3 "$VALIDATOR" "$SIMPLIFIER_SKILL"
mkdir -p "$GLOBAL_ROOT"

install_skill() {
  local name="$1"
  local source="$2"
  local destination="$GLOBAL_ROOT/$name"
  local temporary="$GLOBAL_ROOT/.$name.next"
  if [[ -e "$destination" && ! -L "$destination" ]]; then
    echo "Refusing to replace non-symlink skill: $destination" >&2
    exit 1
  fi
  rm -f "$temporary"
  ln -s "$source" "$temporary"
  mv -f "$temporary" "$destination"
}

install_skill "autonomous-pr-review" "$CUSTOM_SKILL"
install_skill "simplify-pr-implementation" "$SIMPLIFIER_SKILL"

python3 "$SCRIPT_DIR/refresh_context.py" \
  --config "$SCRIPT_DIR/config.json" \
  --manifest "$SCRIPT_DIR/provider-skills.json" \
  --state-root "$SCRIPT_DIR/state" \
  --lock "$SCRIPT_DIR/state/skills.lock.json"

echo "Installed the PR simplifier and autonomous reviewer, then refreshed audited provider context globally."

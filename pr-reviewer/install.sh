#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
CUSTOM_SKILL="$SCRIPT_DIR/skills/autonomous-pr-review"
VALIDATOR="${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py"

if [[ ! -f "$VALIDATOR" ]]; then
  echo "Missing Codex skill validator: $VALIDATOR" >&2
  exit 1
fi

python3 "$VALIDATOR" "$CUSTOM_SKILL"
mkdir -p "$GLOBAL_ROOT"

DESTINATION="$GLOBAL_ROOT/autonomous-pr-review"
if [[ -e "$DESTINATION" && ! -L "$DESTINATION" ]]; then
  echo "Refusing to replace non-symlink skill: $DESTINATION" >&2
  exit 1
fi

TEMPORARY="$GLOBAL_ROOT/.autonomous-pr-review.next"
rm -f "$TEMPORARY"
ln -s "$CUSTOM_SKILL" "$TEMPORARY"
mv -f "$TEMPORARY" "$DESTINATION"

python3 "$SCRIPT_DIR/refresh_context.py" \
  --config "$SCRIPT_DIR/config.json" \
  --manifest "$SCRIPT_DIR/provider-skills.json" \
  --state-root "$SCRIPT_DIR/state" \
  --lock "$SCRIPT_DIR/state/skills.lock.json"

echo "Installed the autonomous reviewer and refreshed audited provider context globally."

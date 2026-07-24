#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
SKILL_NAME="code-maintainer"
SOURCE="$SCRIPT_DIR/skills/$SKILL_NAME"
VALIDATOR="$GLOBAL_ROOT/.system/skill-creator/scripts/quick_validate.py"

if [[ ! -f "$VALIDATOR" ]]; then
  echo "Missing Codex skill validator: $VALIDATOR" >&2
  exit 1
fi

python3 "$VALIDATOR" "$SOURCE"
mkdir -p "$GLOBAL_ROOT"

DESTINATION="$GLOBAL_ROOT/$SKILL_NAME"
if [[ -e "$DESTINATION" && ! -L "$DESTINATION" ]]; then
  echo "Refusing to replace non-symlink skill: $DESTINATION" >&2
  exit 1
fi

TEMPORARY="$GLOBAL_ROOT/.$SKILL_NAME.next"
rm -f "$TEMPORARY"
ln -s "$SOURCE" "$TEMPORARY"
rm -f "$DESTINATION"
mv -f "$TEMPORARY" "$DESTINATION"

echo "Installed $SKILL_NAME globally from $SOURCE."

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-$SCRIPT_DIR/config.json}"
LOG_DIR="$SCRIPT_DIR/logs"
CRON_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$HOME/Library/pnpm"
mkdir -p "$LOG_DIR"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Missing reviewer config: $CONFIG_PATH" >&2
  exit 1
fi

EXISTING=$(crontab -l 2>/dev/null | grep -v "pr-reviewer/reconcile.py" | grep -v "pr-reviewer/refresh_skills.py" | grep -v "^#.*Autonomous PR Reviewer" || true)
NEW_CRONTAB="$EXISTING
# Autonomous PR Reviewer — recovery sweep every 30 minutes
*/30 * * * * /usr/bin/env PATH=\"$CRON_PATH\" python3 \"$SCRIPT_DIR/reconcile.py\" --config \"$CONFIG_PATH\" --apply >> \"$LOG_DIR/reconcile.log\" 2>&1
# Autonomous PR Reviewer — refresh official global skills Sundays at 03:15
15 3 * * 0 /usr/bin/env PATH=\"$CRON_PATH\" python3 \"$SCRIPT_DIR/refresh_skills.py\" --manifest \"$SCRIPT_DIR/provider-skills.json\" --state-root \"$SCRIPT_DIR/state\" --lock \"$SCRIPT_DIR/state/skills.lock.json\" --promote >> \"$LOG_DIR/skill-refresh.log\" 2>&1"

TEMPORARY_CRONTAB="$(mktemp "${TMPDIR:-/tmp}/pr-reviewer-crontab.XXXXXX")"
trap 'rm -f "$TEMPORARY_CRONTAB"' EXIT
printf '%s\n' "$NEW_CRONTAB" | sed '/./,$!d' > "$TEMPORARY_CRONTAB"
crontab "$TEMPORARY_CRONTAB"
echo "Installed PR reviewer recovery and weekly skill-refresh cron entries."

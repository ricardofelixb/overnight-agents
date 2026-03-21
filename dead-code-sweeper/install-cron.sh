#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

# Remove any existing dead-code-sweep entry, preserve other cron jobs
EXISTING=$(crontab -l 2>/dev/null | grep -v "dead-code-sweep" | grep -v "^#.*Dead Code Sweep" || true)

# Build new crontab
NEW_CRONTAB="$EXISTING
# Dead Code Sweep — schedule from config.sh
$SCHEDULE $SCRIPT_DIR/sweep.sh"

# Install (trim leading blank lines)
echo "$NEW_CRONTAB" | sed '/./,$!d' | crontab -

echo "Cron installed: $SCHEDULE $SCRIPT_DIR/sweep.sh"
echo "Verify with: crontab -l"

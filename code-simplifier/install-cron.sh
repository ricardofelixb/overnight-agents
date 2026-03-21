#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

# Remove any existing code-simplifier entry, preserve other cron jobs
EXISTING=$(crontab -l 2>/dev/null | grep -v "code-simplifier" | grep -v "^#.*Code Simplifier" || true)

# Build new crontab
NEW_CRONTAB="$EXISTING
# Code Simplifier — schedule from config.sh
$SCHEDULE $SCRIPT_DIR/simplify.sh"

# Install (trim leading blank lines)
echo "$NEW_CRONTAB" | sed '/./,$!d' | crontab -

echo "Cron installed: $SCHEDULE $SCRIPT_DIR/simplify.sh"
echo "Verify with: crontab -l"

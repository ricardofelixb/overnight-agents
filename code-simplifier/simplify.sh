#!/usr/bin/env bash
set -euo pipefail

# Inert compatibility entrypoint for legacy cron references. The canonical
# LaunchAgent invokes controller.py directly, so this prevents duplicate runs
# until an old crontab line can be removed interactively.
echo "SKIPPED — legacy code-simplifier entrypoint is disabled; use controller.py"

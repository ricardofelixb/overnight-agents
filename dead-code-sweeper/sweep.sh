#!/usr/bin/env bash
set -euo pipefail

# Ensure PATH includes Homebrew (cron runs with minimal PATH)
export PATH="/Users/ricardo/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/.env"
export CLAUDE_CODE_OAUTH_TOKEN
export GH_TOKEN
source "$SCRIPT_DIR/config.sh"

# --- Check enabled ---
if [[ "${ENABLED:-true}" != "true" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DISABLED — skipping sweep"
  exit 0
fi

# --- Setup logging ---
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
LOG_FILE="$LOG_DIR/sweep_${TIMESTAMP}.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# --- Pick next project (round-robin) ---
if [[ -f "$STATE_FILE" ]]; then
  INDEX=$(cat "$STATE_FILE")
else
  INDEX=0
fi

TOTAL=${#PROJECTS[@]}
CURRENT_INDEX=$((INDEX % TOTAL))
NEXT_INDEX=$(((INDEX + 1) % TOTAL))
echo "$NEXT_INDEX" > "$STATE_FILE"

IFS=':' read -r PROJECT_PATH DEFAULT_BRANCH PROJECT_ENABLED <<< "${PROJECTS[$CURRENT_INDEX]}"
PROJECT_NAME=$(basename "$PROJECT_PATH")

# --- Skip disabled projects ---
if [[ "${PROJECT_ENABLED:-true}" != "true" ]]; then
  log "SKIPPED: $PROJECT_NAME is disabled"
  exit 0
fi
BRANCH_NAME="${BRANCH_PREFIX}/$(date +%Y-%m-%d)"

log "=== Dead Code Sweep ==="
log "Project: $PROJECT_NAME ($PROJECT_PATH)"
log "Default branch: $DEFAULT_BRANCH"
log "Branch: $BRANCH_NAME"

# --- Prepare the repo ---
cd "$PROJECT_PATH"

# Abort if working tree is dirty (don't clobber in-progress work)
if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
  log "SKIPPED: working tree is dirty in $PROJECT_NAME — not touching it"
  exit 0
fi

# Fetch, checkout default branch, pull latest
git fetch origin >> "$LOG_FILE" 2>&1
git checkout "$DEFAULT_BRANCH" >> "$LOG_FILE" 2>&1
git pull origin "$DEFAULT_BRANCH" >> "$LOG_FILE" 2>&1
log "Fetched and pulled latest $DEFAULT_BRANCH"

# Install dependencies (so linters/builds reflect current state)
if [[ -f "pnpm-lock.yaml" ]]; then
  pnpm install --frozen-lockfile >> "$LOG_FILE" 2>&1
  log "Installed dependencies (pnpm)"
elif [[ -f "uv.lock" ]]; then
  uv sync >> "$LOG_FILE" 2>&1
  log "Installed dependencies (uv)"
fi

# Create and switch to cleanup branch
if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME" 2>/dev/null; then
  log "Branch $BRANCH_NAME already exists, appending timestamp"
  BRANCH_NAME="${BRANCH_NAME}-$(date +%H%M%S)"
fi
git checkout -b "$BRANCH_NAME" >> "$LOG_FILE" 2>&1
log "Created branch $BRANCH_NAME"

# --- Build the prompt with substitutions ---
PROMPT="${CLAUDE_PROMPT//\{\{PROJECT_PATH\}\}/$PROJECT_PATH}"
PROMPT="${PROMPT//\{\{DEFAULT_BRANCH\}\}/$DEFAULT_BRANCH}"
PROMPT="${PROMPT//\{\{BRANCH_NAME\}\}/$BRANCH_NAME}"

# --- Run Claude ---
log "Starting Claude..."
unset CLAUDECODE
claude --dangerously-skip-permissions -p "$PROMPT" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

log "Claude exited with code $EXIT_CODE"

# --- Cleanup: return to default branch ---
cd "$PROJECT_PATH"
git checkout "$DEFAULT_BRANCH" >> "$LOG_FILE" 2>&1
log "Returned to $DEFAULT_BRANCH"

# --- Prune old logs (keep last 30) ---
ls -1t "$LOG_DIR"/sweep_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true

log "=== Sweep complete ==="

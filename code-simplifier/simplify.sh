#!/usr/bin/env bash
set -euo pipefail

# Ensure PATH includes Homebrew (cron runs with minimal PATH)
export PATH="/Users/ricardo/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  source "$SCRIPT_DIR/.env"
fi
export CLAUDE_CODE_OAUTH_TOKEN
export GH_TOKEN
source "$SCRIPT_DIR/config.sh"

# --- Check enabled ---
if [[ "${ENABLED:-true}" != "true" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] DISABLED — skipping simplify"
  exit 0
fi

# --- Setup logging ---
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
LOG_FILE="$LOG_DIR/simplify_${TIMESTAMP}.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# --- Pick next enabled project (round-robin, skipping disabled) ---
if [[ -f "$STATE_FILE" ]]; then
  INDEX=$(cat "$STATE_FILE")
else
  INDEX=0
fi

TOTAL=${#PROJECTS[@]}
FOUND=false
for (( i=0; i<TOTAL; i++ )); do
  CURRENT_INDEX=$(( (INDEX + i) % TOTAL ))
  IFS=':' read -r PROJECT_PATH DEFAULT_BRANCH PROJECT_ENABLED <<< "${PROJECTS[$CURRENT_INDEX]}"
  if [[ "${PROJECT_ENABLED:-true}" == "true" ]]; then
    FOUND=true
    echo $(( (CURRENT_INDEX + 1) % TOTAL )) > "$STATE_FILE"
    break
  fi
done

if [[ "$FOUND" != "true" ]]; then
  log "SKIPPED: no enabled projects"
  exit 0
fi

PROJECT_NAME=$(basename "$PROJECT_PATH")
BRANCH_NAME="${BRANCH_PREFIX}/$(date +%Y-%m-%d)"
RESUMING_EXISTING_BRANCH=false

log "=== Code Simplifier ==="
log "Project: $PROJECT_NAME ($PROJECT_PATH)"
log "Default branch: $DEFAULT_BRANCH"
log "Branch: $BRANCH_NAME"

# --- Check simplification.md exists ---
if [[ ! -f "$PROJECT_PATH/simplification.md" ]]; then
  log "SKIPPED: no simplification.md in $PROJECT_NAME"
  exit 0
fi

# --- Check if all folders are already done ---
if ! grep -q '\[ \]' "$PROJECT_PATH/simplification.md"; then
  log "SKIPPED: all folders already checked in $PROJECT_NAME"
  exit 0
fi

# --- Prepare the repo ---
cd "$PROJECT_PATH"

CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"

# Abort dirty user/default branches, but resume dirty simplifier branches.
if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
  if [[ "$CURRENT_BRANCH" == "$BRANCH_PREFIX/"* ]]; then
    RESUMING_EXISTING_BRANCH=true
    BRANCH_NAME="$CURRENT_BRANCH"
    log "RESUMING: dirty simplifier branch $BRANCH_NAME"
  else
    log "SKIPPED: working tree is dirty on ${CURRENT_BRANCH:-unknown branch} in $PROJECT_NAME — not touching it"
    exit 0
  fi
fi

if [[ "$RESUMING_EXISTING_BRANCH" != "true" ]]; then
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
fi

# --- Build the prompt with substitutions ---
if [[ "${USE_CODEX:-false}" == "true" ]]; then
  RAW_PROMPT="$CODEX_PROMPT"
else
  RAW_PROMPT="$CLAUDE_PROMPT"
fi
PROMPT="${RAW_PROMPT//\{\{PROJECT_PATH\}\}/$PROJECT_PATH}"
PROMPT="${PROMPT//\{\{DEFAULT_BRANCH\}\}/$DEFAULT_BRANCH}"
PROMPT="${PROMPT//\{\{BRANCH_NAME\}\}/$BRANCH_NAME}"
if [[ "$RESUMING_EXISTING_BRANCH" == "true" ]]; then
  PROMPT="${PROMPT}"$'\n\n## Resume Context\n\n- The working tree already contained uncommitted changes on this simplifier branch before you started.\n- Start by reading `git diff` and continue, fix, validate, and ship that existing work instead of starting a separate folder.\n- Do not discard existing changes unless they are demonstrably wrong or replaced by a safer implementation.'
fi

# --- Run agent ---
set +e
if [[ "${USE_CODEX:-false}" == "true" ]]; then
  log "Starting Codex (gpt-5.5)..."
  codex exec \
    --dangerously-bypass-approvals-and-sandbox \
    -c 'model_reasoning_effort="medium"' \
    -m "gpt-5.5" \
    -C "$PROJECT_PATH" \
    "$PROMPT" >> "$LOG_FILE" 2>&1
  EXIT_CODE=$?
else
  log "Starting Claude..."
  unset CLAUDECODE
  claude --dangerously-skip-permissions --model 'claude-sonnet-5[1m]' --effort medium -p "$PROMPT" >> "$LOG_FILE" 2>&1
  EXIT_CODE=$?
fi
set -e

log "Claude exited with code $EXIT_CODE"

# --- Cleanup: return to default branch ---
cd "$PROJECT_PATH"
if [[ -n $(git status --porcelain 2>/dev/null) ]]; then
  log "LEFT ON $BRANCH_NAME: working tree still has uncommitted changes for a future resume"
else
  git checkout "$DEFAULT_BRANCH" >> "$LOG_FILE" 2>&1
  log "Returned to $DEFAULT_BRANCH"
fi

# --- Prune old logs (keep last 30) ---
ls -1t "$LOG_DIR"/simplify_*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true

log "=== Simplify complete ==="
exit "$EXIT_CODE"

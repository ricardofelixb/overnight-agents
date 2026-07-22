#!/usr/bin/env bash
set -euo pipefail

# Ensure PATH includes Homebrew (schedulers run with a minimal PATH)
export PATH="/Users/ricardo/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prevent launchd, legacy cron, or a manual trigger from overlapping. lockf
# releases the kernel lock automatically even if the child exits unexpectedly.
if [[ "${SIMPLIFIER_LOCK_HELD:-false}" != "true" ]]; then
  SHARED_STATE_DIR="$SCRIPT_DIR/../state"
  mkdir -p "$SHARED_STATE_DIR"
  set +e
  /usr/bin/lockf -s -t 0 -k "$SHARED_STATE_DIR/maintenance.lock" \
    /usr/bin/env SIMPLIFIER_LOCK_HELD=true "$SCRIPT_DIR/simplify.sh" "$@"
  LOCK_EXIT_CODE=$?
  set -e
  if [[ "$LOCK_EXIT_CODE" -eq 75 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIPPED: another maintenance agent is running"
    exit 0
  fi
  exit "$LOCK_EXIT_CODE"
fi

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

CONVEX_LIFECYCLE_ACTIVE=false
cleanup_agent_convex() {
  if [[ "$CONVEX_LIFECYCLE_ACTIVE" != "true" ]]; then
    return
  fi
  CONVEX_LIFECYCLE_ACTIVE=false
  if [[ -x "$PROJECT_PATH/scripts/cleanup-worktree.sh" ]]; then
    "$PROJECT_PATH/scripts/cleanup-worktree.sh" >> "$LOG_FILE" 2>&1 || \
      log "WARNING: failed to delete the worktree-local Convex deployment"
  fi
}
trap cleanup_agent_convex EXIT

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

SOURCE_PROJECT_PATH="$PROJECT_PATH"
PROJECT_NAME=$(basename "$SOURCE_PROJECT_PATH")
BRANCH_NAME="${BRANCH_PREFIX}/$(date +%Y-%m-%d)"
RESUMING_EXISTING_BRANCH=false
PROJECT_ENV_FILE="$PROJECT_ENV_ROOT/$PROJECT_NAME.env.local"
PROJECT_CHECKLIST_FILE="$CHECKLIST_ROOT/$PROJECT_NAME.md"

log "=== Code Simplifier ==="
log "Project: $PROJECT_NAME ($SOURCE_PROJECT_PATH)"
log "Default branch: $DEFAULT_BRANCH"
log "Branch: $BRANCH_NAME"

# --- Prepare the controller-owned clone ---
mkdir -p "$WORKSPACE_ROOT" "$PROJECT_ENV_ROOT" "$CHECKLIST_ROOT"
set +e
WORKSPACE_RESULT=$(
  "$SCRIPT_DIR/workspace.py" \
    --source "$SOURCE_PROJECT_PATH" \
    --workspace-root "$WORKSPACE_ROOT" \
    --project "$PROJECT_NAME" \
    --base-branch "$DEFAULT_BRANCH" \
    --branch-prefix "$BRANCH_PREFIX" \
    --environment-file "$PROJECT_ENV_FILE" \
    --checklist-file "$PROJECT_CHECKLIST_FILE" \
    2>> "$LOG_FILE"
)
WORKSPACE_EXIT_CODE=$?
set -e
if [[ "$WORKSPACE_EXIT_CODE" -ne 0 ]]; then
  log "BLOCKED: could not prepare the isolated simplifier workspace"
  exit "$WORKSPACE_EXIT_CODE"
fi
PROJECT_PATH=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["workspace"])' "$WORKSPACE_RESULT")
RESUMING_EXISTING_BRANCH=$(python3 -c 'import json, sys; print("true" if json.loads(sys.argv[1])["resuming"] else "false")' "$WORKSPACE_RESULT")
if [[ "$RESUMING_EXISTING_BRANCH" == "true" ]]; then
  BRANCH_NAME=$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["branch"])' "$WORKSPACE_RESULT")
  log "RESUMING: dirty simplifier branch $BRANCH_NAME"
fi
log "Workspace: $PROJECT_PATH"
cd "$PROJECT_PATH"

# --- Check simplification.md in the isolated workspace ---
if [[ ! -f "simplification.md" ]]; then
  log "SKIPPED: no simplification.md in $PROJECT_NAME"
  exit 0
fi
if ! grep -q '\[ \]' "simplification.md"; then
  log "SKIPPED: all folders already checked in $PROJECT_NAME"
  exit 0
fi

if [[ "$RESUMING_EXISTING_BRANCH" != "true" ]]; then
  log "Prepared latest origin/$DEFAULT_BRANCH in the isolated workspace"

  # Repositories with the canonical worktree lifecycle install dependencies
  # as part of their setup hook below.
  if [[ -f "pnpm-lock.yaml" && ! -x "scripts/setup-worktree.sh" ]]; then
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

if [[ -x "scripts/setup-worktree.sh" && -x "scripts/cleanup-worktree.sh" ]]; then
  log "Preparing isolated worktree-local Convex deployment"
  scripts/setup-worktree.sh --convex-mode local >> "$LOG_FILE" 2>&1
  CONVEX_LIFECYCLE_ACTIVE=true
  log "Prepared isolated worktree-local Convex deployment"
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
  log "Starting Codex..."
  codex exec \
    --dangerously-bypass-approvals-and-sandbox \
    -c 'model_reasoning_effort="high"' \
    -m "gpt-5.6-terra" \
    -C "$PROJECT_PATH" \
    "$PROMPT" >> "$LOG_FILE" 2>&1
  EXIT_CODE=$?
else
  log "Starting Claude (claude-opus-4-8)..."
  unset CLAUDECODE
  claude --dangerously-skip-permissions --model 'claude-opus-4-8' --effort medium -p "$PROMPT" >> "$LOG_FILE" 2>&1
  EXIT_CODE=$?
fi
set -e

log "Agent exited with code $EXIT_CODE"

cleanup_agent_convex

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

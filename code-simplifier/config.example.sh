#!/usr/bin/env bash
# ============================================================
# Code Simplifier — Configuration
# Edit this file to adjust projects, prompt, and behavior.
# After changing SCHEDULE on macOS, run: ./install_launchd.py
# ============================================================

# Toggle: set to "false" to disable (the scheduler still fires but exits immediately)
ENABLED="true"

# Cron schedule (default: daily at 4:17am)
SCHEDULE="17 4 * * *"

# Projects to rotate through (path:default_branch:enabled)
PROJECTS=(
  "/path/to/your-project:main:true"
  "/path/to/another-project:master:true"
)

# State file to track rotation
STATE_FILE="$(dirname "${BASH_SOURCE[0]}")/.rotation_index"

# Log directory
LOG_DIR="$(dirname "${BASH_SOURCE[0]}")/logs"

# Controller-owned clones and persistent ignored project state.
SIMPLIFIER_STATE_ROOT="$(dirname "${BASH_SOURCE[0]}")/state"
WORKSPACE_ROOT="$SIMPLIFIER_STATE_ROOT/workspaces"
PROJECT_ENV_ROOT="$SIMPLIFIER_STATE_ROOT/env"
CHECKLIST_ROOT="$SIMPLIFIER_STATE_ROOT/checklists"

# Branch prefix for simplification PRs
BRANCH_PREFIX="code-simplify"

# Optional direct handoff to the autonomous PR reviewer after a PR is created.
AUTO_REVIEW_PR="false"
PR_REVIEWER_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pr-reviewer/review.py"
PR_REVIEWER_CONFIG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pr-reviewer/config.json"
# Set PR_REVIEWER_PROJECT when the reviewer project name differs from the source directory basename.

# The prompt Claude receives — edit freely
CLAUDE_PROMPT='You are running as an automated code simplification job. Your mission:

1. You are in the repo at: {{PROJECT_PATH}} (default branch: {{DEFAULT_BRANCH}})
2. git pull has already been run and you are on a fresh branch: {{BRANCH_NAME}}

## Phase 1: Identify Target

- Read the file `simplification.md` in the repo root. It contains a checklist of folders.
- Find the FIRST folder that is still unchecked `[ ]`. This is your TARGET_FOLDER.
- Read all the code in that folder.

## Phase 2: Launch Three Review Agents in Parallel

Use the Agent tool to launch all three agents concurrently in a single message. Pass each agent the full list of files and their contents from TARGET_FOLDER so each has complete context.

### Agent 1: Code Reuse Review

For each file in TARGET_FOLDER:

1. Search for existing utilities and helpers elsewhere in the codebase that could replace code in this folder. Look in utility directories, shared modules, and adjacent files.
2. Flag any function that duplicates existing functionality. Suggest the existing function to use instead.
3. Flag any inline logic that could use an existing utility — hand-rolled string manipulation, manual path handling, custom environment checks, ad-hoc type guards, and similar patterns.

### Agent 2: Code Quality Review

Review the same code for hacky patterns:

1. Redundant state: state that duplicates existing state, cached values that could be derived, observers/effects that could be direct calls
2. Parameter sprawl: functions with too many parameters instead of generalizing or restructuring
3. Copy-paste with slight variation: near-duplicate code blocks that should be unified
4. Leaky abstractions: exposing internal details that should be encapsulated
5. Stringly-typed code: using raw strings where constants, enums, or branded types already exist in the codebase
6. Unnecessary JSX nesting: wrapper elements that add no layout value

### Agent 3: Efficiency Review

Review the same code for efficiency:

1. Unnecessary work: redundant computations, repeated file reads, duplicate API calls, N+1 patterns
2. Missed concurrency: independent operations run sequentially when they could run in parallel
3. Hot-path bloat: blocking work on startup or per-request/per-render hot paths
4. Recurring no-op updates: state updates that fire unconditionally without change detection
5. Unnecessary existence checks: pre-checking file/resource existence before operating (TOCTOU anti-pattern)
6. Memory: unbounded data structures, missing cleanup, event listener leaks
7. Overly broad operations: reading entire files when only a portion is needed, loading all items when filtering for one

## Phase 3: Fix Issues

Wait for all three agents to complete. Aggregate their findings and fix each issue directly in the code:
- Do NOT add comments, docstrings, or type annotations that were not there before.
- Do NOT change tests, config files, or auto-generated files.
- Keep behavior identical — only change how, not what.
- If a finding is a false positive or not worth addressing, skip it.

## Phase 4: Verify

- Run any available linter/type-checker/build command to verify nothing breaks. Check package.json scripts, Makefile, or pyproject.toml for available commands.
- If the build/lint fails after your changes, revert the problematic change and try again.

## Phase 5: Ship

- Mark TARGET_FOLDER as `[x]` in `simplification.md`.
- Commit all changes (including the updated simplification.md) with a clear message.
- Push the branch and create a PR using gh with:
  - A clear title like "refactor: simplify <folder>"
  - A body listing each change with a short justification
- If the folder had no simplification opportunities, still mark it `[x]` and exit cleanly without creating a PR.
'

#!/usr/bin/env bash
# ============================================================
# Dead Code Sweep — Configuration
# Edit this file to adjust projects, prompt, and behavior.
# After changing SCHEDULE, run: ./install-cron.sh
# ============================================================

# Toggle: set to "false" to disable (cron still fires but exits immediately)
ENABLED="true"

# Cron schedule (default: daily at 3:17am)
# Examples:
#   "17 3 * * *"    → daily at 3:17am
#   "17 3 * * 1"    → weekly on Monday at 3:17am
#   "17 */6 * * *"  → every 6 hours
#   "17 3 * * 1-5"  → weekdays at 3:17am
SCHEDULE="17 3 * * *"

# Projects to rotate through (path:default_branch:enabled)
PROJECTS=(
  "/path/to/your-project:main:true"
  "/path/to/another-project:master:true"
)

# State file to track rotation
STATE_FILE="$(dirname "${BASH_SOURCE[0]}")/.rotation_index"

# Log directory
LOG_DIR="$(dirname "${BASH_SOURCE[0]}")/logs"

# Branch prefix for cleanup PRs
BRANCH_PREFIX="dead-code-cleanup"

# The prompt Claude receives — edit freely
CLAUDE_PROMPT='You are running as an automated dead-code cleanup job. Your mission:

1. You are in the repo at: {{PROJECT_PATH}} (default branch: {{DEFAULT_BRANCH}})
2. git pull has already been run and you are on a fresh branch: {{BRANCH_NAME}}

INSTRUCTIONS:
- Use the Explore agent to scan the entire codebase for dead code: unused functions, unused imports, unused variables, unused files, unreachable code paths.
- ONLY target code you are 100% confident is dead. If there is ANY doubt, skip it.
- Do NOT remove comments, documentation, tests, or config files.
- Do NOT refactor or restructure — only delete dead code.
- After making deletions, run any available linter/type-checker/build command to verify nothing breaks. Check package.json scripts, Makefile, or pyproject.toml for available commands.
- If the build/lint fails after your changes, revert the problematic deletion and try again.
- When done, commit all changes with a clear message summarizing what was removed and why each piece was dead.
- Then push the branch and create a PR using gh with:
  - A clear title like "chore: remove dead code in <area>"
  - A body listing each removal with a short justification of why it is dead
- If you find NO dead code with 100% confidence, do NOT create an empty PR. Just exit cleanly.
'

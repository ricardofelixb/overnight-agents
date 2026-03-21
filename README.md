# Automations

Autonomous code maintenance agents powered by [Claude Code](https://docs.anthropic.com/en/docs/claude-code). They run on cron, rotate through your projects, and open PRs with their changes.

## Automations

### dead-code-sweeper

Scans codebases for unused functions, imports, variables, files, and unreachable code paths. Opens PRs with removals when confident.

### code-simplifier

Works through your codebase folder-by-folder using a checklist (`simplification.md`). For each folder, spawns three review agents in parallel:

1. **Code Reuse** — finds existing utilities that could replace duplicated code
2. **Code Quality** — flags redundant state, copy-paste, parameter sprawl, leaky abstractions
3. **Efficiency** — catches N+1 patterns, missed concurrency, unnecessary work, memory leaks

Aggregates findings, fixes the code, verifies with linter/build, and opens a PR.

#### How the checklist works

Create a `simplification.md` in each target repo with a folder tree:

```markdown
# Simplification Checklist

## src/
- [ ] components/auth/
- [ ] components/dashboard/
- [ ] lib/
- [ ] hooks/
```

Add `simplification.md` to `.gitignore`. Each run picks the next unchecked folder, does the work, and marks it `[x]`. Progress persists across runs without polluting git history.

## Setup

1. Clone this repo
2. Copy the example files in each automation you want to use:
   ```
   cp .env.example dead-code-sweeper/.env
   cp dead-code-sweeper/config.example.sh dead-code-sweeper/config.sh

   cp .env.example code-simplifier/.env
   cp code-simplifier/config.example.sh code-simplifier/config.sh
   ```
3. Fill in your tokens in `.env`
4. Edit each `config.sh` — set your project paths, branches, and schedule
5. For code-simplifier: create `simplification.md` in each target repo and add it to `.gitignore`
6. Install the cron jobs:
   ```
   ./dead-code-sweeper/install-cron.sh
   ./code-simplifier/install-cron.sh
   ```

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- macOS/Linux with cron

## Configuration

Each automation has a `config.sh` with:

- **`ENABLED`** — global toggle (`true`/`false`)
- **`SCHEDULE`** — cron expression
- **`PROJECTS`** — array of `path:default_branch:enabled` entries

```bash
PROJECTS=(
  "/path/to/repo-a:main:true"
  "/path/to/repo-b:master:false"   # temporarily disabled
)
```

Projects rotate round-robin. Disabled projects are skipped.

## Safety

- Skips repos with dirty working trees
- Returns to the default branch after each run
- Verifies changes with linter/build before committing
- Keeps the last 30 log files, prunes older ones

## Manual run

```bash
./dead-code-sweeper/sweep.sh
./code-simplifier/simplify.sh
```

## Logs

Each automation writes timestamped logs to its `logs/` directory.

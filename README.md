# Automations

Autonomous code maintenance agents powered by Codex or Claude Code. They run on cron, rotate through configured projects, and open narrowly scoped pull requests.

## Automations

### dead-code-sweeper

Scans codebases for unused functions, imports, variables, files, and unreachable code paths. Opens PRs with removals when confident.

### code-simplifier

Works through your codebase folder-by-folder using a checklist (`simplification.md`). For each folder, spawns three review agents in parallel:

1. **Code Reuse** — finds existing utilities that could replace duplicated code
2. **Code Quality** — flags redundant state, copy-paste, parameter sprawl, leaky abstractions
3. **Efficiency** — catches N+1 patterns, missed concurrency, unnecessary work, memory leaks

Aggregates findings, fixes the code, verifies with linter/build, and opens a PR.

Simplifier runs use controller-owned clones under `code-simplifier/state/workspaces/`. The configured source checkout is used only to discover the trusted origin URL, so local branches, staged files, and unfinished work are never switched, copied, or included in a simplification PR. A dirty interrupted `code-simplify/*` branch is resumed only inside the automation clone; unexpected workspace contamination is quarantined and replaced.

Ignored automation state is stored outside the clone and symlinked into it. Each enabled project uses:

- `code-simplifier/state/env/<project>.env.local` for private runtime configuration (mode `0600`)
- `code-simplifier/state/checklists/<project>.md` when `simplification.md` is ignored rather than tracked

The same private environment file can be configured for the PR reviewer through its per-project `environment_file`. Both controllers require `.env.local` to be ignored and refuse unmanaged or broadly readable environment files.

### pr-reviewer

Reviews an exact pull-request base/head pair after the simplifier publishes it. One Codex orchestrator spawns three specialist sub-agents for behavior/contracts, security/provider boundaries, and hygiene/tests. It reconciles their evidence, reads SHA-bound PR comments and reviews as untrusted leads, consults allowlisted current provider documentation and promoted official skills, and directly repairs every proven bounded issue in the touched behavioral slice. Repairs may address introduced defects, pre-existing defects, valid PR follow-ups, security hardening, performance, or worthwhile code hygiene.

The orchestrator may edit but cannot commit, push, comment on GitHub, approve, merge, or delete branches. After edits it uses a fresh verifier sub-agent. A deterministic controller owns the exact-SHA workspace, runs full validation before and after the orchestrator, checks the reported working-tree files, commits and pushes verified repairs, and maintains one idempotent PR comment explaining either “safe to merge,” “fixed and safe to merge,” or the exact blocking decision. The user remains the final merger.

Reviewer workspaces are disposable controller-owned clones. First-run and interrupted `--no-checkout` clones are checked out before cleanliness is evaluated; contaminated or incomplete workspaces are moved to an auditable quarantine and replaced atomically. Successful or blocked results are cached by PR head and update timestamp so the recovery schedule does not repeat unchanged reviews.

Projects may define non-secret validation resource settings such as `NODE_OPTIONS` in `validation_environment`. Credential-like and critical shell variables are rejected, and repository commands never inherit arbitrary automation secrets.

The reviewer defaults to `repair`. `observe` remains available for a read-only dry run, while the local exac deployment uses `repair`: verified changes are pushed to the PR branch and manual merge remains in GitHub.

A semantic blocker sends one deduplicated outbound Telegram notification with the decision needed. Delivery uses no webhook or inbound listener. Failed sends remain in a durable outbox and are retried by the recovery schedule; notification failure never changes the PR review result.

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
   cp pr-reviewer/config.example.json pr-reviewer/config.json
   cp pr-reviewer/.env.example pr-reviewer/.env
   ```
3. Fill in your tokens in `.env`
4. Edit each `config.sh` — set your project paths, branches, and schedule
5. For code-simplifier:
   - Put the project environment at `code-simplifier/state/env/<project>.env.local` and run `chmod 600` on it.
   - If `simplification.md` is ignored, store its canonical copy at `code-simplifier/state/checklists/<project>.md` and symlink the project checkout's `simplification.md` to it.
   - If `simplification.md` is tracked, keep using the tracked repository file.
   - Configure the reviewer project's `environment_file` to point at the same private environment file.
6. Install the cron jobs:
   ```
   ./dead-code-sweeper/install-cron.sh
   ./code-simplifier/install-cron.sh
   ```
7. Install the reviewer skill/provider bundle globally, then install its recovery and weekly-refresh schedules:
   ```bash
   ./pr-reviewer/install.sh
   ./pr-reviewer/install-cron.sh
   ```
   On macOS, use the native custom scheduler if `crontab` is unavailable:
   ```bash
   ./pr-reviewer/install_launchd.py
   ```

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Codex CLI](https://developers.openai.com/codex/cli/) (`codex`)
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

- Never switches or modifies the configured source checkout, even when it is dirty
- Uses a dedicated simplifier clone and returns that clone to the default branch after clean runs
- Persists ignored checklists outside disposable automation clones
- Requires private project environment files and ignored workspace symlinks
- Verifies changes with linter/build before committing
- Keeps the last 30 log files, prunes older ones
- Refuses reviewer-policy, workflow, dependency-manifest, and provider-guidance changes
- Uses a dedicated clone and refuses dirty or overlapping review workspaces
- Repairs only proven, bounded changes with unambiguous intended behavior, regardless of whether the defect is introduced or pre-existing
- Fails closed on stale docs/skills, unsafe scope, stale SHAs, schema mismatch, or validation mutation

## Manual run

```bash
./dead-code-sweeper/sweep.sh
./code-simplifier/simplify.sh
```

## Logs

Each automation writes timestamped logs to its `logs/` directory.

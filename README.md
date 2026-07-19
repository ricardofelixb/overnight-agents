# Automations

Autonomous code-maintenance agents powered by Codex or Claude Code. Scheduled agents create narrowly scoped pull requests; the event-driven PR reviewer independently simplifies, reviews, repairs, validates, and summarizes every eligible human-authored PR.

## Automations

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

Reviews every eligible human-authored pull request at an exact base/head pair. One Codex orchestrator spawns three specialist sub-agents for behavior/contracts, security/provider boundaries, and an independent simplification/hygiene pass. It reconciles their evidence, reads SHA-bound PR comments, reviews, and GitHub CI logs as untrusted leads, consults allowlisted current provider documentation and promoted official skills, and directly repairs every proven bounded issue in the touched behavioral slice. Repairs may address introduced defects, pre-existing defects, valid PR follow-ups, security hardening, performance, worthwhile code hygiene, or a reproducible validation-gate failure.

GitHub webhooks are the primary trigger for PR open, reopen, ready-for-review, synchronize, human review-feedback, and failed check-suite events. The loopback-only receiver verifies `X-Hub-Signature-256`, durably deduplicates deliveries, and processes reviews sequentially outside the HTTP request. Dependabot is excluded. A 30-minute reconciliation sweep remains only as recovery for deliveries missed while the machine was offline.

```text
GitHub event
  -> signed loopback webhook receiver
  -> durable, delivery-ID-deduplicated queue
  -> exact-SHA PR controller
  -> behavior/contracts + security/provider + simplification/hygiene
  -> fresh verifier
  -> full validation gate
  -> verified repair commit or idempotent PR summary
```

Provider routing uses progressive disclosure. The controller detects broad candidate domains and verifies a trusted catalog of fresh, hashed skills and official documentation, but Codex opens only the smallest skill topic and document required by a concrete code question. Candidate domains do not require provider evidence when repository code, types, tests, and project rules are sufficient. Fresh documentation caches are reused without another network request.

The orchestrator may edit but cannot commit, push, comment on GitHub, approve, merge, or delete branches. After edits it uses a fresh verifier sub-agent. A deterministic controller owns the exact-SHA workspace, captures the initial full-validation result as repair evidence, checks the reported working-tree files, requires the complete gate to pass before any repair push or clean recommendation, and maintains one idempotent PR comment explaining either “safe to merge,” “fixed and safe to merge,” or the exact blocking decision. The user remains the final merger.

Reviewer workspaces are disposable controller-owned clones. First-run and interrupted `--no-checkout` clones are checked out before cleanliness is evaluated; contaminated or incomplete workspaces are moved to an auditable quarantine and replaced atomically. Successful or blocked results are cached by PR head and update timestamp so the recovery schedule does not repeat unchanged reviews.

Projects may define non-secret validation resource settings such as `NODE_OPTIONS` in `validation_environment`. Credential-like and critical shell variables are rejected, and repository commands never inherit arbitrary automation secrets.

The reviewer defaults to `repair`. `observe` remains available for a read-only dry run, while the local exac deployment uses `repair`: verified changes are pushed to the PR branch and manual merge remains in GitHub. The scheduled simplifier does not invoke the reviewer directly; publishing its PR produces the same webhook event as a human PR, avoiding duplicate expensive reviews.

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

1. Clone this repository.
2. Copy the example files for the automations you want to enable:

   ```bash
   cp .env.example code-simplifier/.env
   cp code-simplifier/config.example.sh code-simplifier/config.sh
   cp pr-reviewer/config.example.json pr-reviewer/config.json
   cp pr-reviewer/.env.example pr-reviewer/.env
   ```

3. Set private environment files to owner-only access:

   ```bash
   chmod 600 pr-reviewer/.env
   chmod 600 code-simplifier/state/env/*.env.local
   ```

   Do not manually invent a webhook secret. `configure_webhook.py` creates and persists one without printing it.

4. Edit the local ignored configuration files with project paths, repositories, base branches, and validation commands.
5. For `code-simplifier`:

   - Put the project environment at `code-simplifier/state/env/<project>.env.local` and run `chmod 600` on it.
   - If `simplification.md` is ignored, store its canonical copy at `code-simplifier/state/checklists/<project>.md` and symlink the project checkout's `simplification.md` to it.
   - If `simplification.md` is tracked, keep using the tracked repository file.
   - Configure the reviewer project's `environment_file` to point at the same private environment file.

6. Install the scheduled simplifier with launchd on macOS:

   ```bash
   ./code-simplifier/install_launchd.py
   ```

7. Install the reviewer skill and promoted provider bundle globally:

   ```bash
   ./pr-reviewer/install.sh
   ```

8. On macOS, install the launchd services for the persistent webhook receiver, recovery sweep, and weekly provider-context refresh:

   ```bash
   ./pr-reviewer/install_launchd.py
   ```

   `install-cron.sh` remains available for the recovery and refresh schedules on systems using cron; it does not supervise the persistent HTTP receiver.

9. Expose only the loopback receiver through a dedicated public Tailscale Funnel:

   ```bash
   tailscale funnel --bg --yes --https 8443 --set-path /github-webhook http://127.0.0.1:8765
   ```

10. Create or update the signed GitHub repository hook through `gh` without exposing its secret:

   ```bash
   ./pr-reviewer/configure_webhook.py \
     --repository owner/repository \
     --url https://your-host.example.ts.net:8443/github-webhook \
     --env ./pr-reviewer/.env
   ```

   The hook subscribes to `pull_request`, `pull_request_review`, `pull_request_review_comment`, and `check_suite`. Human feedback or a failed check forces a new exact-SHA repair review even when that head was reviewed previously.

The weekly refresh uses isolated temporary clones. It promotes audited provider skills globally and runs `npx convex ai-files update` for each enabled Convex project, publishing a hashed guidance snapshot without modifying the configured source checkout.

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Codex CLI](https://developers.openai.com/codex/cli/) (`codex`)
- [Tailscale](https://tailscale.com/) with Funnel enabled for inbound GitHub webhooks
- macOS with launchd for the persistent webhook receiver; cron remains supported for scheduled recovery/refresh jobs

## Configuration

### Scheduled agents

Each automation has a `config.sh` with:

- **`ENABLED`** — global toggle (`true`/`false`)
- **`SCHEDULE`** — daily minute/hour cron expression, translated to launchd on macOS
- **`PROJECTS`** — array of `path:default_branch:enabled` entries

```bash
PROJECTS=(
  "/path/to/repo-a:main:true"
  "/path/to/repo-b:master:false"   # temporarily disabled
)
```

Projects rotate round-robin. Disabled projects are skipped.

### PR reviewer

`pr-reviewer/config.json` is local and ignored. Each enabled project defines its repository, source checkout, base branch, environment file, and exact validation commands. To review every same-repository human branch while excluding Dependabot:

```json
{
  "name": "example",
  "enabled": true,
  "source_path": "/absolute/path/to/project",
  "repository": "owner/repository",
  "base_branch": "main",
  "allowed_head_patterns": ["*"],
  "excluded_authors": ["dependabot[bot]", "app/dependabot"],
  "allow_forks": false,
  "validation_commands": [["pnpm", "run", "validate:github"]],
  "mode": "repair"
}
```

Draft PRs wait for `ready_for_review`. Cross-repository PRs remain blocked unless explicitly enabled, because a repair push must never target an untrusted fork. Provider skills and documentation are selected on demand from signed/fresh manifests rather than loaded eagerly.

### Validation self-healing

The initial validation gate is evidence, not a pre-review dismissal:

1. Capture local validation output and current GitHub check metadata at the immutable PR head.
2. Include failed GitHub Actions step logs when available.
3. Ask the specialists and orchestrator to reproduce and repair a bounded code cause.
4. Never modify protected CI policy, weaken tests, or loosen types/lint to obtain green status.
5. If a reviewer-authored repair fails validation, feed the exact failure back into up to two focused correction cycles without repeating the full specialist review.
6. Require a fresh verifier and the complete configured validation command to pass before pushing a repair or posting a clean recommendation.

If the failure is external, transient, ambiguous, or unsafe to repair, the reviewer reports the precise blocker instead of guessing.

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
- Never posts a clean result for a reproducible red validation gate; repairs must pass the full configured validation before being pushed
- Verifies webhook HMAC signatures and keeps the secret only in an ignored mode-`0600` environment file

## Manual run

```bash
./code-simplifier/simplify.sh

# Review one exact PR with verified repairs enabled.
./pr-reviewer/review.py \
  --config ./pr-reviewer/config.json \
  --project example \
  --pr 123 \
  --apply

# Run the missed-delivery recovery sweep.
./pr-reviewer/reconcile.py --config ./pr-reviewer/config.json --apply
```

Do not use `--force` for ordinary retries. It intentionally bypasses the reviewed-head cache and is reserved for new review feedback or a failed check on the same SHA.

## Operations

Verify the local receiver, public Funnel, and launchd process:

```bash
curl --fail http://127.0.0.1:8765/healthz
curl --fail https://your-host.example.ts.net:8443/github-webhook/healthz
tailscale funnel status
launchctl print gui/$(id -u)/com.overnight-agents.pr-reviewer-webhook
```

Inspect webhook metadata without retrieving its secret:

```bash
gh api repos/owner/repository/hooks \
  --jq '.[] | {id,active,events,url:.config.url,last_response}'
```

Uninstall the reviewer launchd jobs with:

```bash
./pr-reviewer/install_launchd.py --uninstall
```

Disable the dedicated Funnel separately:

```bash
tailscale funnel --https 8443 off
```

## Logs and state

- `code-simplifier/logs/` — simplifier run history
- `pr-reviewer/logs/webhook.log` — receiver health and HTTP status lines; request bodies and signatures are never logged
- `pr-reviewer/logs/webhook-worker.log` — queued review controller output
- `pr-reviewer/logs/reconcile.log` — recovery sweep output
- `pr-reviewer/logs/context-refresh.log` — weekly provider-skill, documentation, and Convex AI-files refresh
- `pr-reviewer/state/webhook-queue/` — pending and in-progress signed deliveries
- `pr-reviewer/state/webhook-deliveries/` — bounded delivery receipts used for deduplication
- `pr-reviewer/state/runs/<project>/<pr>/` — immutable inputs, validation evidence, orchestrator result, and summary for each run

All runtime state, logs, local configuration, and secret-bearing environment files are ignored by Git.

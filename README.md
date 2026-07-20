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

### codebase-organizer

Executes one approved, behavior-preserving source-organization slice at a time from `organization.md`. Each checklist item binds a correlated Convex domain, frontend domain, route implementation, adapters, generated references, and tests. Internal source vocabulary is English; client-facing Spanish copy and routes remain unchanged.

The organizer performs clean atomic refactors: it removes old paths and updates every repository-controlled caller without barrels, forwarding modules, aliases, compatibility shims, or legacy fallbacks. A reusable `codebase-organizer` skill defines canonical folder and filename patterns, while each project's persistent checklist defines its exact approved moves.

Runs use an isolated controller-owned clone, a global and per-project enable switch, and one active organizer PR per project. The deterministic controller owns the definitive validation, commit, push, and PR creation. Organizer branches skip the generic PR simplification pass and proceed directly to the existing correctness/security PR reviewer.

`code-simplifier` and `codebase-organizer` share `state/maintenance.lock`, preventing overlapping scheduled maintenance even if both LaunchAgents are enabled.

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

### pr-simplifier skill

`pr-simplifier/skills/simplify-pr-implementation` is the default first-pass skill for eligible human PRs. It binds work to exact base/head SHAs and uses three read-only specialists for reuse, maintainability, and efficiency; the orchestrator may retain only behavior-preserving improvements that pass an independent verifier. It is PR-slice scoped rather than folder scoped, and it does not certify correctness, recommend merging, or replace `pr-reviewer`.

The existing webhook queue owns one atomic lifecycle: human PR -> PR simplifier -> full validation and local simplification checkpoint -> ordinary PR reviewer in the same workspace -> full validation -> one final branch push and one idempotent comment. Nothing from the simplifier is pushed or commented independently. Before the single lease-protected push, the controller verifies that the remote PR branch still equals the original GitHub head; the lease makes that comparison atomic, so a concurrent human push aborts the publication instead of being overwritten. Exact-head state prevents the final controller-authored push from recursively starting another simplification pass. A later human push creates a new head and receives a fresh pass. Scheduled `code-simplify/*` PRs already received their simplification pass and proceed directly to `pr-reviewer`.

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
   cp codebase-organizer/config.example.json codebase-organizer/config.json
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

   Install the codebase organizer skill and scheduled LaunchAgent when using organization checklists:

   ```bash
   ./codebase-organizer/install.sh
   ./codebase-organizer/install_launchd.py
   ```

   Keep only one of the simplifier or organizer globally enabled while performing broad structural work. Their shared maintenance lock still prevents accidental overlap.

7. Install the human-PR simplifier, reviewer skill, and promoted provider bundle globally:

   ```bash
   ./pr-reviewer/install.sh
   ```

   `pr-simplifier/install.sh` remains available when only the reusable simplification skill is needed.

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

`codebase-organizer/config.json` uses the same global/project enable model in JSON. Every project additionally supplies a private environment file, persistent checklist path, repository, base branch, and definitive validation command. The exac organizer uses `pnpm run validate`, which includes the full test suite and `convex dev --once`.

The organization checklist uses one top-level item per correlated vertical slice:

```markdown
- [ ] **sales** — Align the complete sales product domain
  - Backend: `convex/receipts/` → `convex/sales/`
  - Frontend: `src/components/receipts/` → `src/components/sales/`
  - Preserve: `/ventas`, Spanish copy, receipt entity vocabulary, and wire contracts.
  - Remove: old paths, aliases, barrels, forwarding files, and fallbacks.
```

The marker becomes complete only when the source refactor is ready and the controller's definitive validation passes. An open organizer PR blocks later checklist items; a closed-unmerged organizer PR restores its item to unchecked.

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
5. Retry a failed complete gate according to `validation_attempts` before spending another agent turn on a potentially transient failure.
6. If the reviewer returns schema-valid but semantically contradictory JSON, run one bounded result-only correction without repeating specialist review; verifier status describes retained edits, not the separate controller/CI gate.
7. If a reviewer-authored repair still fails validation, feed the exact failure back into up to two focused correction cycles without repeating the full specialist review.
8. Require a fresh verifier and the complete configured validation command to pass before pushing a repair or posting a clean recommendation.

If the failure is external, transient, ambiguous, or unsafe to repair, the reviewer reports the precise blocker instead of guessing.

## Safety

- Never switches or modifies the configured source checkout, even when it is dirty
- Uses a dedicated simplifier clone and returns that clone to the default branch after clean runs
- Persists ignored checklists outside disposable automation clones
- Requires private project environment files and ignored workspace symlinks
- Verifies changes with linter/build before committing
- Keeps the last 30 log files, prunes older ones
- Refuses PRs that alter trusted reviewer policy, workflow, or generated provider guidance; dependency manifests and lockfiles remain reviewable input but are immutable to the agents
- Uses a dedicated clone and refuses dirty or overlapping review workspaces
- Repairs only proven, bounded changes with unambiguous intended behavior, regardless of whether the defect is introduced or pre-existing
- Fails closed on stale docs/skills, unsafe scope, stale SHAs, schema mismatch, or validation mutation
- Never posts a clean result for a reproducible red validation gate; repairs must pass the full configured validation before being pushed
- Verifies webhook HMAC signatures and keeps the secret only in an ignored mode-`0600` environment file

## Manual run

```bash
./code-simplifier/simplify.sh

# Inspect the next organization item without editing.
./codebase-organizer/organize.py --project exac

# Execute one organization item and publish its validated PR.
./codebase-organizer/organize.py --project exac --apply

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
- `codebase-organizer/logs/` — organizer run history
- `codebase-organizer/state/checklists/` — persistent project organization checklists
- `codebase-organizer/state/workspaces/` — isolated organizer clones
- `codebase-organizer/state/pending/` — active organizer PR/item state
- `pr-reviewer/logs/webhook.log` — receiver health and HTTP status lines; request bodies and signatures are never logged
- `pr-reviewer/logs/webhook-worker.log` — queued review controller output
- `pr-reviewer/logs/reconcile.log` — recovery sweep output
- `pr-reviewer/logs/context-refresh.log` — weekly provider-skill, documentation, and Convex AI-files refresh
- `pr-reviewer/state/webhook-queue/` — pending and in-progress signed deliveries
- `pr-reviewer/state/webhook-deliveries/` — bounded delivery receipts used for deduplication
- `pr-reviewer/state/runs/<project>/<pr>/` — immutable inputs, validation evidence, orchestrator result, and summary for each run

All runtime state, logs, local configuration, and secret-bearing environment files are ignored by Git.

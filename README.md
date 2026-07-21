# Automations

Autonomous code-maintenance agents powered by Codex or Claude Code. Scheduled agents create narrowly scoped pull requests; an explicit PR comment command starts the independent simplification, review, repair, validation, and summary lifecycle when a pull request is ready.

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

Runs use an isolated controller-owned clone, a global and per-project enable switch, and one active organizer PR per project. The deterministic controller owns the definitive validation, commit, push, and PR creation. Organizer PRs can be reviewed explicitly with `/review`; they never launch another simplification pass automatically.

`code-simplifier` and `codebase-organizer` share `state/maintenance.lock`, preventing overlapping scheduled maintenance even if both LaunchAgents are enabled.

### pr-reviewer

Reviews an eligible pull request at an exact base/head pair after an authorized owner, member, or collaborator posts the exact comment `/review`. One Codex orchestrator spawns three specialist sub-agents for behavior/contracts, security/provider boundaries, and an independent simplification/hygiene pass. It reconciles their evidence, reads SHA-bound PR comments, reviews, and GitHub CI logs as untrusted leads, consults allowlisted current provider documentation and promoted official skills, and directly repairs every proven bounded issue in the touched behavioral slice. Repairs may address introduced defects, pre-existing defects, valid PR follow-ups, security hardening, performance, worthwhile code hygiene, or a reproducible validation-gate failure.

The GitHub webhook subscribes only to `issue_comment`. The loopback-only receiver verifies `X-Hub-Signature-256`, requires a newly created comment on an open PR, authorizes the signed GitHub `OWNER`, `MEMBER`, or `COLLABORATOR` association, maps `/review` to only the reviewer and `/simplify` to only the PR simplifier, durably deduplicates the delivery, and processes jobs sequentially outside the HTTP request. Both commands fail closed before launching an agent unless GitHub CI is green for the exact PR head. Pushes, PR lifecycle events, CI events, reviews, ordinary comments, edited commands, comments on non-PR issues, and unauthorized commands never start a cycle. Dependabot remains excluded by policy.

```text
Authorized `/review` PR comment
  -> signed loopback webhook receiver
  -> durable, delivery-ID-deduplicated queue
  -> require green GitHub CI for the exact head
  -> exact-SHA PR controller
  -> behavior/contracts + security/provider + simplification/hygiene reviewer
  -> fresh verifier
  -> local validation only when the reviewer edits code
  -> verified repair commit or idempotent PR summary

Authorized `/simplify` PR comment
  -> same receiver, queue, CI gate, and exact-SHA controller
  -> PR simplifier only
  -> full local validation when code changes
  -> lease-protected simplification commit (no review comment)
```

Provider routing uses progressive disclosure. The controller detects broad candidate domains and verifies a trusted catalog of fresh, hashed skills and official documentation, but Codex opens only the smallest skill topic and document required by a concrete code question. Candidate domains do not require provider evidence when repository code, types, tests, and project rules are sufficient. Fresh documentation caches are reused without another network request.

The orchestrator may edit but cannot commit, push, comment on GitHub, approve, merge, or delete branches. After edits it uses a fresh verifier sub-agent. A deterministic controller owns the exact-SHA workspace, checks the reported working-tree files, validates reviewer edits, and maintains one idempotent PR comment explaining either “safe to merge,” “fixed and safe to merge,” or the exact blocking decision. A clean read-only review relies on the exact-head green GitHub CI admission evidence instead of rerunning the same local gate. The user remains the final merger.

Reviewer workspaces are disposable controller-owned clones. First-run and interrupted `--no-checkout` clones are checked out before cleanliness is evaluated; contaminated or incomplete workspaces are moved to an auditable quarantine and replaced atomically. Successful or blocked results are cached by PR head and update timestamp for ordinary controller safety; every new authorized `/review` delivery intentionally requests a fresh cycle, even on the same SHA.

Projects may define non-secret validation resource settings such as `NODE_OPTIONS` in `validation_environment`. Credential-like and critical shell variables are rejected, and repository commands never inherit arbitrary automation secrets.

The reviewer defaults to `repair`. `observe` remains available for a read-only dry run, while the local exac deployment uses `repair`: verified changes are pushed to the PR branch and manual merge remains in GitHub. Neither human pushes nor scheduled simplifier PR publication invokes the reviewer automatically; comment `/review` once the PR is ready.

A semantic blocker sends one deduplicated outbound Telegram notification with the decision needed. Delivery uses no webhook or inbound listener. Failed sends remain in a durable outbox and are retried by a notification-only schedule; notification failure never changes the PR review result.

### pr-simplifier skill

`pr-simplifier/skills/simplify-pr-implementation` is an explicitly requested pass for eligible PRs. It binds work to exact base/head SHAs and uses three read-only specialists for reuse, maintainability, and efficiency; the orchestrator may retain only behavior-preserving improvements that pass an independent verifier. It is PR-slice scoped rather than folder scoped, and it does not certify correctness, recommend merging, or replace `pr-reviewer`.

The two commands are intentionally independent. `/simplify` runs only the simplifier, requires full local validation for any edit, and pushes one lease-protected commit without posting a reviewer comment. `/review` runs only the correctness/security reviewer and posts its idempotent summary; it runs local validation only if it repairs code. Neither command automatically launches the other. Before any push, the controller verifies that the remote PR branch still equals the original GitHub head; the lease makes that comparison atomic, so a concurrent human push aborts publication instead of being overwritten.

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

8. On macOS, install the launchd services for the persistent webhook receiver, outbound-notification retry, and weekly provider-context refresh:

   ```bash
   ./pr-reviewer/install_launchd.py
   ```

   `install-cron.sh` remains available for notification retry and context refresh on systems using cron; it does not supervise the persistent HTTP receiver or start reviews.

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

   The hook subscribes only to `issue_comment`. Post `/review` for only a correctness/security review or `/simplify` for only a behavior-preserving simplification pass. Run either command after GitHub CI is green. A new authorized command intentionally starts a fresh exact-SHA job even if the same head was handled previously.

The weekly refresh uses isolated temporary clones. It promotes audited provider skills globally and runs `npx convex ai-files update` for each enabled Convex project, publishing a hashed guidance snapshot without modifying the configured source checkout.

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Codex CLI](https://developers.openai.com/codex/cli/) (`codex`)
- [Tailscale](https://tailscale.com/) with Funnel enabled for inbound GitHub webhooks
- macOS with launchd for the persistent webhook receiver; cron remains supported for notification retry and provider-context refresh

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
  "review_comment_commands": ["/review"],
  "simplify_comment_commands": ["/simplify"],
  "review_comment_author_associations": ["OWNER", "MEMBER", "COLLABORATOR"],
  "allow_forks": false,
  "validation_commands": [["pnpm", "run", "validate"]],
  "mode": "repair"
}
```

Draft PRs remain ineligible even if someone comments `/review`. Cross-repository PRs remain blocked unless explicitly enabled, because a repair push must never target an untrusted fork. Provider skills and documentation are selected on demand from signed/fresh manifests rather than loaded eagerly.

### Validation self-healing

The commands own their validation independently:

1. Require green GitHub CI for the exact input head before either agent starts.
2. `/simplify` runs only the simplifier; if it edits code, the controller runs the full configured validation before committing or pushing.
3. `/review` runs only the reviewer; a read-only result reuses the green exact-head CI evidence, while any repair must pass full local validation before it is committed or pushed.
4. A failing locally changed tree returns to the responsible agent for focused correction and revalidation without a numeric attempt ceiling or another full discovery pass.
5. Correction continues while it makes progress and stops only on green, an evidence-backed blocker, a changed remote head, or a repeated no-progress state.
6. Never modify protected CI policy, weaken tests, or loosen types/lint to obtain green status.

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
  --operation review \
  --apply

# Simplify one exact PR without launching the reviewer.
./pr-reviewer/review.py \
  --config ./pr-reviewer/config.json \
  --project example \
  --pr 123 \
  --operation simplify \
  --apply

# Retry pending outbound notifications; this never starts a review.
./pr-reviewer/reconcile.py --config ./pr-reviewer/config.json
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
- `pr-reviewer/logs/reconcile.log` — outbound-notification retry output
- `pr-reviewer/logs/context-refresh.log` — weekly provider-skill, documentation, and Convex AI-files refresh
- `pr-reviewer/state/webhook-queue/` — pending and in-progress signed deliveries
- `pr-reviewer/state/webhook-deliveries/` — bounded delivery receipts used for deduplication
- `pr-reviewer/state/runs/<project>/<pr>/` — immutable inputs, validation evidence, orchestrator result, and summary for each run

All runtime state, logs, local configuration, and secret-bearing environment files are ignored by Git.

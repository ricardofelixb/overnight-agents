# Automations

Autonomous code-maintenance agents powered by Codex or Claude Code. Scheduled agents create narrowly scoped pull requests; an explicit PR comment command starts the independent simplification, review, repair, validation, and summary lifecycle when a pull request is ready.

## Automations

### code-maintainer

Continuously audits versioned semantic codebase slices instead of relying on a
path checkbox. Every slice routes five read-only specialists:

1. **Reuse and simplification** — deletes proven duplication and unnecessary indirection.
2. **Maintainability and organization** — enforces the project's canonical ownership, folder, filename, casing, and colocation policy.
3. **Efficiency and performance** — finds reachable repeated, unbounded, or avoidably serial work.
4. **Correctness and reliability** — proves and repairs violated behavior and lifecycle invariants.
5. **Security hardening** — validates reachable authorization, tenant, input, trust-boundary, and data-exposure defects.

The editing orchestrator independently verifies every specialist finding,
applies one bounded coherent change, runs focused and definitive validation,
and uses a fresh verifier. The controller enforces protected paths, file and
diff budgets, commits the returned tree, pushes a `code-maintain/*` branch, and
opens the PR.

Project context uses progressive disclosure. Every specialist receives Exac's
core invariants and current audited guidance; only the organization specialist
receives the canonical source constitution, while the other roles receive
their own ownership, performance, correctness, or security context. The
controller fails closed when required hashed provider skills, Convex AI files,
or official documentation are stale or fail integrity checks.

Semantic slice state lives under `code-maintainer/state/cycles/`. A no-change
audit advances immediately. A changed slice advances only after its PR merges;
a closed-unmerged PR retries the same semantic slice. Finishing the final slice
increments the cycle and starts again automatically. Stable slice IDs survive
folder and filename changes.

Runs use isolated shared workspaces under `automation/`. Exac uses
`scripts/setup-worktree.sh --convex-mode local` and
`scripts/cleanup-worktree.sh`, giving every run a private local Convex backend.
Dirty interrupted `code-maintain/*` work is preserved for resume; unexpected
workspaces are quarantined.

The maintainer's organization role supersedes the retired standalone organizer.
The shared `state/maintenance.lock` remains the single schedule-overlap guard.

### pr-reviewer

Reviews an eligible pull request at an exact base/head pair after an authorized owner, member, or collaborator posts the exact comment `/review`. One Codex orchestrator spawns three specialist sub-agents for behavior/contracts, security/provider boundaries, and an independent simplification/hygiene pass. It reconciles their evidence, reads SHA-bound PR comments, reviews, and GitHub CI logs as untrusted leads, consults allowlisted current provider documentation and promoted official skills, and directly repairs every proven bounded issue in the touched behavioral slice. Repairs may address introduced defects, pre-existing defects, valid PR follow-ups, security hardening, performance, worthwhile code hygiene, or a reproducible validation-gate failure.

The GitHub webhook subscribes only to `issue_comment`. The loopback-only receiver verifies `X-Hub-Signature-256`, requires a newly created comment on an open PR, authorizes the signed GitHub `OWNER`, `MEMBER`, or `COLLABORATOR` association, maps `/review` to only the reviewer and `/simplify` to only the PR simplifier, durably deduplicates the delivery, and processes jobs sequentially outside the HTTP request. Both commands fail closed before launching an agent unless GitHub CI is green for the exact PR head. Pushes, PR lifecycle events, CI events, reviews, ordinary comments, edited commands, comments on non-PR issues, and unauthorized commands never start a cycle. Dependabot remains excluded by policy.

Every accepted command receives a best-effort 👀 reaction and one delivery-scoped progress comment. The shared progress reporter edits that comment at controller milestones and refreshes its timestamp every configured heartbeat interval instead of posting repeated replies. It records completion, a safety blocker, or an unexpected worker failure for both commands. Progress publishing is operational telemetry only: GitHub API or permission failures are logged and never change the underlying review or simplification result. The token used by `gh` needs `Issues: write` for the reaction; creating and updating the progress comment accepts `Issues: write` or `Pull requests: write`.

```text
Authorized `/review` PR comment
  -> signed loopback webhook receiver
  -> durable, delivery-ID-deduplicated queue
  -> require green GitHub CI for the exact head
  -> exact-SHA PR controller
  -> behavior/contracts + security/provider + simplification/hygiene reviewer
  -> agent-owned validation and fresh verifier
  -> lease-protected repair commit or idempotent PR summary

Authorized `/simplify` PR comment
  -> same receiver, queue, CI gate, and exact-SHA controller
  -> PR simplifier only
  -> agent-owned validation and iteration
  -> lease-protected simplification commit (no review comment)
```

Provider routing uses progressive disclosure. The controller detects broad candidate domains and verifies a trusted catalog of fresh, hashed skills and official documentation, but Codex opens only the smallest skill topic and document required by a concrete code question. Candidate domains do not require provider evidence when repository code, types, tests, and project rules are sufficient. Fresh documentation caches are reused without another network request.

The orchestrator may edit but cannot commit, push, comment on GitHub, approve, merge, or delete branches. It owns validation, may diagnose and fix failures freely, and uses a fresh verifier sub-agent after edits. The minimal controller owns authorization, the exact-SHA workspace, protected-file policy, commit/push mechanics, lease safety, progress publishing, and cleanup. It does not rerun validation, launch validation-correction agents, or reject work over semantic result-contract bookkeeping. GitHub CI on the pushed head remains authoritative, and the user remains the final merger.

Reviewer workspaces are disposable controller-owned clones. First-run and interrupted `--no-checkout` clones are checked out before cleanliness is evaluated; contaminated or incomplete workspaces are moved to an auditable quarantine and replaced atomically. Successful or blocked results are cached by PR head and update timestamp for ordinary controller safety; every new authorized `/review` delivery intentionally requests a fresh cycle, even on the same SHA.

Projects provide validation commands and non-secret resource settings such as `NODE_OPTIONS` directly to the agent. Credential-like and critical shell variables are rejected. Agent and dependency-setup processes prefer the Node version declared by the repository's `.nvmrc` or `.node-version`, preventing the LaunchAgent's global PATH from silently selecting another runtime.

The reviewer defaults to `repair`. `observe` remains available for a read-only dry run, while the local exac deployment uses `repair`: verified changes are pushed to the PR branch and manual merge remains in GitHub. Neither human pushes nor scheduled maintainer PR publication invokes the reviewer automatically; comment `/review` once the PR is ready.

A semantic blocker sends one deduplicated outbound Telegram notification with the decision needed. Delivery uses no webhook or inbound listener. Failed sends remain in a durable outbox and are retried by a notification-only schedule; notification failure never changes the PR review result.

### pr-simplifier skill

`pr-simplifier/skills/simplify-pr-implementation` is an explicitly requested pass for eligible PRs. It binds work to exact base/head SHAs and uses three read-only specialists for reuse, maintainability, and efficiency; the orchestrator may retain only behavior-preserving improvements that pass an independent verifier. It is PR-slice scoped rather than folder scoped, and it does not certify correctness, recommend merging, or replace `pr-reviewer`.

The two commands are intentionally independent. `/simplify` runs only the simplifier and `/review` runs only the correctness/security reviewer. Each agent owns its validation and may iterate until it considers the work ready; the controller does not repeat that work. Neither command automatically launches the other. Before any push, the controller verifies that the remote PR branch still equals the original GitHub head; the lease makes that comparison atomic, so a concurrent human push aborts publication instead of being overwritten.

## Setup

1. Clone this repository.
2. Copy the example files for the automations you want to enable:

   ```bash
   cp .env.example code-maintainer/.env
   cp code-maintainer/config.example.json code-maintainer/config.json
   cp pr-reviewer/config.example.json pr-reviewer/config.json
   cp pr-reviewer/.env.example pr-reviewer/.env
   ```

3. Set private environment files to owner-only access:

   ```bash
   chmod 600 pr-reviewer/.env
   chmod 600 code-maintainer/state/env/*.env.local
   ```

   Do not manually invent a webhook secret. `configure_webhook.py` creates and persists one without printing it.

4. Edit the local ignored configuration files with project paths, repositories, base branches, and validation commands.
5. For `code-maintainer`:

   - Put clone-workspace project environments at `code-maintainer/state/env/<project>.env.local` and run `chmod 600` on them.
   - Add the project profile and semantic slice registry under `code-maintainer/skills/code-maintainer/references/projects/<project>/`.
   - Configure the audited skill lock, AI-files root, official docs catalog, and docs cache produced by `pr-reviewer/refresh_context.py`.
   - Configure the reviewer project's `environment_file` to point at the same private environment file.

6. Install the scheduled maintainer with launchd on macOS:

   ```bash
   ./code-maintainer/install.sh
   ./code-maintainer/install_launchd.py
   ```

   The installer manages only `com.overnight-agents.code-maintainer`.

7. Install the human-PR simplifier, reviewer skill, and promoted provider bundle globally:

   ```bash
   ./pr-reviewer/install.sh
   ```

   `pr-simplifier/install.sh` remains available when only the reusable simplification skill is needed.

8. On macOS, install the launchd services for the persistent webhook receiver, outbound-notification retry, and weekly provider-context refresh:

   ```bash
   ./pr-reviewer/install_launchd.py
   ```

   This deployment uses launchd for notification retry and provider-context refresh.

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

   Configure `workspace_hooks` for repository-owned setup and cleanup. For Exac, both webhook commands run the canonical `scripts/setup-worktree.sh --convex-mode local` from the trusted source checkout against the exact-head review clone, then always run `scripts/cleanup-worktree.sh`. Each top-level command gets one private local Convex deployment; all specialists inside that command use it, and it is deleted when the command finishes.

   Progress feedback is enabled by default. Configure `github_progress_enabled` and `github_progress_heartbeat_seconds` in defaults or per project; the heartbeat interval must be between 60 and 3600 seconds.

The weekly refresh uses isolated temporary clones. It promotes audited provider skills globally and runs `npx convex ai-files update` for each enabled Convex project, publishing a hashed guidance snapshot without modifying the configured source checkout.

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [GitHub CLI](https://cli.github.com/) (`gh`)
- [Codex CLI](https://developers.openai.com/codex/cli/) (`codex`)
- [Tailscale](https://tailscale.com/) with Funnel enabled for inbound GitHub webhooks
- macOS with launchd for scheduled maintenance and the persistent webhook receiver

## Configuration

### Scheduled maintainer

The maintainer configuration includes:

- **`enabled`** — global and per-project boolean switches
- **`schedule`** — daily minute/hour expression translated to launchd calendar intervals
- **`provider`** and model settings — shared Codex or Claude invocation policy
- **`projects`** — named repository, base branch, validation, and workspace policy objects
- **`context`** — audited skill lock, AI-files snapshots, and official-documentation refresh paths
- **change budgets** — maximum changed files and diff bytes per autonomous PR

Projects rotate round-robin. Disabled projects are skipped.

Every project supplies a versioned project profile and semantic slice registry,
repository, base branch, and validation commands. Clone workspaces also require
a private environment file. A linked-worktree project instead configures
repository-relative `setup_command` and `cleanup_command` arrays. Exac selects
canonical worktree setup with `--convex-mode local`; cleanup removes its
private backend before removing the worktree.

```json
"workspace": {
  "type": "linked-worktree",
  "setup_command": ["scripts/setup-worktree.sh", "--convex-mode", "local"],
  "cleanup_command": ["scripts/cleanup-worktree.sh"],
  "management_token_file": "/private/convex-management.token"
}
```

Semantic slice selectors are discovery anchors, not static authorization.
Agents resolve the current owner by imports, routes, adapters, and tests. The
cycle is keyed by the stable slice ID, so canonical renames do not stale
controller progress.

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
  "github_progress_enabled": true,
  "github_progress_heartbeat_seconds": 900,
  "review_comment_author_associations": ["OWNER", "MEMBER", "COLLABORATOR"],
  "allow_forks": false,
  "validation_commands": [["pnpm", "run", "validate"]],
  "mode": "repair"
}
```

Draft PRs remain ineligible even if someone comments `/review`. Cross-repository PRs remain blocked unless explicitly enabled, because a repair push must never target an untrusted fork. Provider skills and documentation are selected on demand from signed/fresh manifests rather than loaded eagerly.

### Agent-owned validation

Every reviewer, PR simplifier, and scheduled maintainer owns one complete review, edit, validation, and verification lifecycle:

1. The controller binds an exact PR head or semantic maintenance slice and prepares its isolated workspace.
2. The agent receives the repository validation commands and safe environment settings.
3. The agent runs focused or full validation, diagnoses failures, fixes relevant defects, and iterates as often as useful in the same lifecycle.
4. The agent distinguishes failures caused by its edits from unrelated, flaky, environmental, or already-green exact-head CI failures.
5. The controller trusts that judgment, records the agent's evidence, and never starts a second validation or JSON-correction cycle.
6. The controller commits only safe returned working-tree changes. PR-comment agents push with an exact-head lease; scheduled agents publish a new automation branch and PR.

If the failure is external, transient, ambiguous, or unsafe to repair, the reviewer reports the precise blocker instead of guessing.

## Safety

- Never switches or modifies the configured source checkout, even when it is dirty
- Uses controller-owned clones or linked worktrees without switching the configured source checkout
- Lets repository-owned lifecycle hooks provision isolated external services for linked worktrees
- Keeps controller management tokens out of agent-visible worktrees and removes terminally clean linked worktrees
- Persists semantic cycle and pending-PR state outside disposable workspaces
- Requires private project environment files and ignored workspace symlinks
- Gives the agent the repository's validation commands and declared runtime
- Keeps the last 30 log files, prunes older ones
- Refuses PRs that alter trusted reviewer policy, workflow, or generated provider guidance; dependency manifests and lockfiles remain reviewable input but are immutable to the agents
- Uses a dedicated clone and refuses dirty or overlapping review workspaces
- Repairs only proven, bounded changes with unambiguous intended behavior, regardless of whether the defect is introduced or pre-existing
- Fails closed on stale docs/skills, unsafe scope, stale SHAs, protected-file edits, or a changed remote head
- Does not discard agent-verified changes because of unrelated local failures or result-contract formatting
- Verifies webhook HMAC signatures and keeps the secret only in an ignored mode-`0600` environment file

## Manual run

```bash
./code-maintainer/controller.py --project exac --apply

# Inspect the next semantic slice without editing.
./code-maintainer/controller.py --project exac

# Review one exact PR with verified repairs enabled.
./pr-reviewer/controller.py \
  --config ./pr-reviewer/config.json \
  --project example \
  --pr 123 \
  --operation review \
  --apply

# Simplify one exact PR without launching the reviewer.
./pr-reviewer/controller.py \
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

- `code-maintainer/logs/` — maintenance run history
- `code-maintainer/state/cycles/` — perpetual semantic cycle positions
- `code-maintainer/state/pending/` — active maintenance PR and slice state
- `code-maintainer/state/context/` — audited skills, AI-files, and official-doc evidence
- `code-maintainer/state/workspaces/` — isolated maintainer clones or linked worktrees
- `pr-reviewer/logs/webhook.log` — receiver health and HTTP status lines; request bodies and signatures are never logged
- `pr-reviewer/logs/webhook-worker.log` — queued review controller output
- `pr-reviewer/logs/reconcile.log` — outbound-notification retry output
- `pr-reviewer/logs/context-refresh.log` — weekly provider-skill, documentation, and Convex AI-files refresh
- `pr-reviewer/state/webhook-queue/` — pending and in-progress signed deliveries
- `pr-reviewer/state/webhook-deliveries/` — bounded delivery receipts used for deduplication
- `pr-reviewer/state/runs/<project>/<pr>/` — immutable inputs, validation evidence, orchestrator result, and summary for each run

All runtime state, logs, local configuration, and secret-bearing environment files are ignored by Git.

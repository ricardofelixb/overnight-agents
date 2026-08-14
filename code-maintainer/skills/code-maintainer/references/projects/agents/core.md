# Agents core invariants

This monorepo is the agents application. The local Mac checkout is development
only. Production runs on the shared `agents-runtime` Compute Engine VM.

Read the workspace `AGENTS.md` first. It overrides this profile.

Preserve:

- the split between reusable `core/` runtime and company-owned behavior;
- company-specific config, prompts, data-source interpretation, schedules,
  terminology, and business rules inside `companies/<company_slug>/`;
- Python/package-safe company slugs and hyphenated infra slugs derived from
  `company_slug.replace("_", "-")`;
- Telegram as the production user interface, including webhook ownership on the
  VM and dry-run local development that does not steal production webhooks;
- a reserved static external IP for the production VM;
- `ToolDefinition` as the only custom-tool schema; provider adapters translate
  it, and business-critical calculations stay deterministic in Python;
- production deploys as Git mirrors of `origin/main` via pull, not manual VM
  copies;
- ignored runtime files, secrets, and the nested `dashboard/` checkout as a
  separate repository.

Tests and current project documentation are contract evidence. A simpler or
shorter implementation is better only when these invariants remain explicit.

# Agents-app core invariants

The canonical repository is `ricardofelixb/agents-app`. Maintenance covers the
whole monorepo: Next.js web, Electron desktop, Expo mobile, shared packages,
Convex, Python runtime, company behavior, and workspace infrastructure.
Read root `AGENTS.md` first and the nearest package instructions. Read
`runtime/AGENTS.md` for Python/company work. Use the repository feature map
when ownership is unclear and follow current imports and tests.

Preserve:

- clients in `apps/`, transport-neutral contracts and foundations in `packages/`,
  the shared backend in `convex/`, and execution/company behavior in `runtime/`;
- WorkOS ownership of identity, organizations, memberships, credentials, and
  permissions; derive Convex tenant scope from verified identity;
- shared conversation, roster, voice, notification, routine, and module
  contracts across clients, Convex, and runtime;
- reusable Python behavior in `runtime/core/` and company config, prompts,
  data interpretation, schedules, and rules in `runtime/companies/`;
- `ToolDefinition` as the custom-tool schema and deterministic Python
  ownership of business-critical calculations;
- Python-safe company slugs and derived hyphenated infrastructure slugs;
- local development isolation and production Telegram webhook ownership;
- production on `agents-runtime-la`, with `/opt/agents/runtime` as Python
  root, the reserved static IP, and explicit merged CI-validated SHA releases
  through `agents deploy`; client-only changes must not restart runtime services;
- secrets, generated runtime state, and provider credential homes staying ignored.

Use pnpm from the root and uv from `runtime/`. There is one root `.git`.
Scheduled maintenance performs bounded edits and diff inspection; repository
CI owns validation. Respect draft-PR and release authorization instructions.

# Agents-app authoritative guidance

Use current root/package `AGENTS.md`, package versions, types, tests, and
contracts first. Then use controller-supplied audited context evidence, its
exact hashed skills, and official-document cache entries for the concrete
mechanism. Load only the smallest relevant topic.

For Convex, read `convex/_generated/ai/guidelines.md` and `docs/convex.md`.
For clients/auth, read `docs/architecture.md` and `docs/authentication.md`.
For runtime/company work, read `runtime/AGENTS.md`, relevant runtime docs,
and company instructions. Cross-package integration follows
`runtime/docs/dashboard-integrations.md`; tracing follows `docs/tracing.md`.

React, Convex, and WorkOS slices select those audited guidance domains.
Python-only slices select none. The monorepo inherits the shared provider
context rather than opting out globally.

Scheduled maintainers run only final diff inspection and `git diff --check`.
The repository's `CI` and `Runtime Quality` workflows validate the published
head after the repository's draft/merge authorization requirements are met.
Focused local verification by explicitly invoked reviewers uses pnpm at the
root and uv from `runtime/`; full validation belongs to CI.

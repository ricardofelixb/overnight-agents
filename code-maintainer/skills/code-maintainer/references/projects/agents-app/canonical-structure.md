# Agents-app canonical source structure

Keep ownership explicit across `apps/`, `packages/`, `convex/`, and `runtime/`.
See `docs/architecture.md` for package dependency direction. Shared packages
are narrow semantic owners, not generic helpers, utils, or common drawers.
Platform-specific components, sessions, and adapters stay with their client.

Keep Python module namespaces and source/test mirrors intact:

```text
runtime/core/sdk/
runtime/tests/core/sdk/
runtime/companies/exac/
runtime/companies/exac/agents/<agent>/tests/
```

Company slugs use Python-safe snake_case. Derived infrastructure names use
hyphens. Shared template evaluations live under
`runtime/core/agent_templates/<template>/evals/`.

Use existing naming conventions in each TypeScript owner and descriptive
snake_case Python modules. Preserve framework-required filenames. Avoid
empty/decorative folders and broad barrel re-exports. Extract shared behavior
only when multiple concrete consumers prove shared ownership.

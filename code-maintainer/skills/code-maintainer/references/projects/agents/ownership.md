# Agents ownership map

- `core/` owns shared runtime behavior and reusable building blocks: provider
  wrappers, SDK abstractions, integrations, delivery plumbing, scheduling,
  deploy webhooks, generic agent conventions, and utilities that are genuinely
  useful across companies.
- `companies/<company_slug>/` owns company-specific config, prompts, payload
  emitters, API/data-source interpretation, business rules, report definitions,
  schedules, terminology, mappings, workflows, and company documentation.
- `companies/<company_slug>/tools/` owns company-wide tools.
  `companies/<company_slug>/agents/<agent_name>/tools/` owns agent-specific
  tools. Shared tools belong in `core/`.
- `core/sdk/tools/base.py` owns `ToolDefinition`. Provider adapters translate
  that schema; they do not invent provider-only custom tool shapes in company
  code.
- `scripts/` owns repository setup, lint, and developer CLI helpers.
- `infra/` owns VM, systemd, Caddy, and deploy-time machine configuration.
- `tests/core/` owns core behavioral proof. Company tests live beside the
  company agent they cover.

Do not move company-specific assumptions into core because they are useful for
one company. Do not copy core runtime policy into a company package. Before
changing a company agent, inspect that company's config, docs, data source, and
workflow directly.

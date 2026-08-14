# Agents canonical source structure

This document is normative for maintainability findings. Equivalent
responsibilities use the same location and naming pattern across the codebase.
Do not copy responsibilities a domain does not have.

## Vertical identity

Keep one English internal owner across correlated layers:

```text
core/sdk/                  # reusable runtime owner
tests/core/sdk/            # behavioral mirror
companies/exac/            # company-owned behavior
companies/exac/agents/collection/tests/  # company behavioral mirror
```

Company directories use Python-safe `snake_case` slugs. Infra names derived
from a company slug use hyphens. Do not introduce mixed internal naming.

## Core vs company

`core/` is the compact shared-runtime reference. Keep only genuinely reusable
modules there. A company directory is the compact company-behavior reference:

```text
companies/<company_slug>/
├── config.yaml
├── agents/
│   └── <agent_name>/
│       ├── AGENTS.md
│       ├── prompts/
│       ├── tools/
│       ├── runtime/ or application/
│       └── tests/
└── docs/
```

Do not create empty folders or decorative one-file folders. Prefer the actual
role or semantic owner over generic `helpers/`, `utils/`, or `misc/`.

## Filenames

- Python module: descriptive `snake_case.py`.
- Test: match the subject, such as `test_runtime.py`.
- Shell entrypoints used by cron or local dry-runs remain executable scripts
  next to the agent they launch.
- Framework-required names remain exact.

Do not introduce internal `camelCase` Python modules, root `utils.py`, or
barrel `__init__.py` files that re-export the world. Preserve external
snake-case fields only when an official wire or provider contract requires
them.

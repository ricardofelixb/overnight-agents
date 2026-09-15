## Scheduled Code Maintainer

`controller.py` rotates through versioned semantic slices forever. It prepares
an isolated workspace, verifies fresh hashed skills, Convex AI guidance, and
official documentation, invokes the `code-maintainer` skill, publishes a
bounded PR, and advances cycle state only after a no-change audit, a discarded
oversized tree, or a merged PR. The objective is a smaller codebase without
regressions: production source must shrink or stay equal, except for a
correctness or security fix within `max_source_growth_lines`. Codex sessions
are persisted with pending PRs. A completed GitHub workflow failure resumes
that exact session for a bounded repair, while periodic reconciliation covers
missed webhook delivery.

- `controller.py` — lifecycle, safety budgets, size gate, publication, and pending PRs
- `sizing.py` — staged-diff measurement shared by the controller and the agent
- `profiles.py` — project manifests, role routing, and semantic slices
- `cycles.py` — atomic perpetual-cycle state
- `context_evidence.py` — audited skills, AI-files, and official docs
- `policy.py` — JSON configuration validation
- `config.example.json` — configuration template
- `skills/code-maintainer/` — orchestrator, specialist roles, and project policy
- `slice_repair.py` — one-shot Codex repair when slice selectors are stale
- `ci_repair.py` — exact-head CI status recording and same-session repair
- `install_launchd.py` — per-project schedule installer and legacy-label migration
- `state/` and `logs/` — ignored runtime state

Use `./controller.py --project <name> --apply` for a manual run. Never place
prompts or canonical project policy in ignored configuration. Add a project
under `skills/code-maintainer/references/projects/<name>/` and validate its
`profile.json` and `slices.json` before enabling it. Each project owns its
`schedule`; launchd installs one job per enabled project. Root `context` is
the shared audited Convex/React/WorkOS skill/docs cache. A TypeScript/Convex
project inherits it, or sets a sparse overlay. A Python runtime such as
`agents` sets `"context": false` so it never loads that pipeline. Slice
`guidance_domains` still decide which inherited artifacts a run loads.

The optional root-level `agents` object temporarily enables or disables known
specialist roles with booleans. Omitted roles default to enabled. At least one
specialist must remain enabled; project profiles and slice registries continue
to define the complete canonical role set.

On a stale selector during `--apply`, `slice_repair.py` runs one Codex
`gpt-5.6-luna` / medium pass against this repository, the controller verifies
and pushes `main`, and the same job continues. Dry runs still fail closed.

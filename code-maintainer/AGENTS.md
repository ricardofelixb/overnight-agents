## Scheduled Code Maintainer

`controller.py` rotates through versioned semantic slices forever. It prepares
an isolated workspace, verifies fresh hashed skills, Convex AI guidance, and
official documentation, invokes the `code-maintainer` skill, publishes a
bounded PR, and advances cycle state only after a no-change audit or merged PR.

- `controller.py` — lifecycle, safety budgets, publication, and pending PRs
- `profiles.py` — project manifests, role routing, and semantic slices
- `cycles.py` — atomic perpetual-cycle state
- `context_evidence.py` — audited skills, AI-files, and official docs
- `policy.py` — JSON configuration validation
- `config.example.json` — configuration template
- `skills/code-maintainer/` — orchestrator, specialist roles, and project policy
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

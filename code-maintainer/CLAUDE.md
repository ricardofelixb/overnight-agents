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
- `install_launchd.py` — native schedule installer and legacy-label migration
- `state/` and `logs/` — ignored runtime state

Use `./controller.py --project <name> --apply` for a manual run. Never place
prompts or canonical project policy in ignored configuration. Add a project
under `skills/code-maintainer/references/projects/<name>/` and validate its
`profile.json` and `slices.json` before enabling it.

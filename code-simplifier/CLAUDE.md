## Scheduled Code Simplifier

The canonical runtime is `controller.py`. It rotates through projects in the ignored `config.json`, selects one checklist slice, prepares an isolated workspace through `automation/`, invokes the dedicated `skills/code-simplifier` orchestrator, publishes safe returned changes, and cleans up.

- `controller.py` — selection, safety, publication, and lifecycle controller
- `policy.py` — JSON configuration validation
- `config.example.json` — configuration template
- `skills/code-simplifier/` — agent-owned review, edit, validation, and verifier workflow
- `install_launchd.py` — shared JSON-based launchd installer
- `simplify.sh` — inert compatibility shim that prevents old cron entries from running duplicate work
- `.env` — controller credentials; never print or copy it
- `state/` and `logs/` — ignored runtime state

Use `./controller.py --project <name> --apply` for a manual scheduled run. Update `config.json` to change projects, schedules, provider, or validation commands. Do not put workflow prompts in configuration; the versioned skill is the canonical behavior contract.

# Agents-app correctness and security rules

Prove tenant, actor, credential, and lifecycle scope across the complete path:
client -> verified Convex identity -> authenticated company runtime. WorkOS
owns organizations and membership; client-provided organization ids are not
authority. Preserve cross-organization and cross-actor denial.

Check native secure storage, refresh-token ownership, Electron isolation,
exact-origin preload/IPC exposure, external navigation, notification routing,
voice cleanup, and organization changes across mounted modules. Backend
projections must not expose runtime credentials or internal storage ids.

Keep company config and data isolated. Preserve webhook authenticity, replay
protection, production Telegram ownership, and local dry-run behavior. Custom
tools use `runtime/core/sdk/tools/base.py`'s `ToolDefinition`; authoritative
business calculations stay deterministic in Python. Traces/logs/prompts must
not reveal secrets or other tenants' data.

Production releases require an explicit merged CI-validated SHA and the
repository CLI; do not pull latest main on the VM, copy tracked code, or
restart runtime services for client-only changes.

Use behavioral tests beside each owner, including denied paths for access
changes. Preserve authorized behavior and do not weaken CI or test rules.

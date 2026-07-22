# Shared automation lifecycle

The `automation` package is the shared mechanical layer used by the runtime agents:

- `clones.py` provisions and quarantines dedicated clone fallbacks;
- `worktrees.py` owns linked-worktree preparation, repository hooks, controller-only cleanup tokens, and removal;
- `runtime.py` owns streamed commands, declared Node runtime selection, bounded test concurrency, environment loading, and Codex/Claude invocation;
- `launchd.py` owns JSON schedule parsing and native LaunchAgent definitions.

Controllers supply repository-relative setup and cleanup commands. The repository remains the source of truth for project-specific provisioning such as dependency installation, isolated service deployments, environment synchronization, and seeding.

Together these modules guarantee that:

- the configured source checkout is never switched or reset;
- automation runs from a linked worktree based on the latest remote base branch;
- dirty automation branches are preserved for resume;
- failed setup hooks run repository cleanup and remove newly created worktrees;
- unrelated or legacy workspaces are quarantined;
- lifecycle hooks cannot escape the worktree;
- Python setup hooks cannot leave bytecode artifacts in the worktree;
- agent validation uses the repository runtime without launching duplicate background validations;
- management tokens are loaded only for cleanup from a private controller-owned file;
- terminally clean worktrees and their local automation branches are removed.

The management-token file contains only the raw token and must have mode `0600`. It is never copied into the worktree or inherited by the coding agent.

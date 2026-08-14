# Agents authoritative guidance

Use evidence in this order:

1. current workspace `AGENTS.md`, package versions, types, tests, and domain
   contracts;
2. the controller-supplied audited context-evidence JSON;
3. the exact hashed skill release named by that evidence for a concrete
   mechanism;
4. the exact official-document cache entry named by its manifest.

The controller blocks work when required skill releases or official
documentation are stale or fail integrity checks. Do not substitute model
memory, blogs, search snippets, or an unverified installed skill.

Load progressively:

- Security mechanism: the narrow installed Codex Security skill, while keeping
  repository reachability and tests as primary proof.

Read `docs/local-development.md`, `docs/runtime-behavior.md`, and the relevant
company `AGENTS.md` when a company agent is in scope. Run focused tests for the
changed behavior, then use `.venv/bin/python -m pytest` as the definitive
isolated worktree gate.

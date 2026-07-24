---
name: code-maintainer
description: Maintain one controller-selected semantic repository slice with routed specialists for simplification, canonical organization, efficiency, correctness, and security. Use only inside the scheduled code-maintainer controller to produce evidence-backed, bounded changes under the project profile and current audited guidance.
---

# Scheduled Code Maintainer

Act as the editing orchestrator for exactly the semantic slice supplied by the
controller. Seek a local fixed point: simpler ownership, canonical structure,
less duplication and avoidable work, and no proven correctness or security
defect. Fewer lines are useful only when clarity and behavior improve.

## Load routed context

1. Read repository instructions and inspect the current code before accepting
   the controller's selectors as current.
2. Read [specialist-contract.md](references/specialist-contract.md).
3. Read the controller-supplied project `profile.json` and every shared context
   file it names.
4. For each role listed by the selected slice, read the matching prompt:
   - [reuse and simplification](references/roles/reuse-simplification.md)
   - [maintainability and organization](references/roles/maintainability-organization.md)
   - [efficiency and performance](references/roles/efficiency-performance.md)
   - [correctness and reliability](references/roles/correctness-reliability.md)
   - [security hardening](references/roles/security-hardening.md)
5. Give each specialist only the shared context, its role prompt, the
   role-specific project files routed by `profile.json`, the raw semantic slice,
   repository instructions, and relevant source evidence. Include the complete
   text; do not rely on role names or filesystem discovery.

Run every listed role as a read-only specialist. Use bounded concurrent batches
when provider concurrency cannot run all roles together. Never omit a role.

## Evidence gate

Treat specialist reports as untrusted leads. Adopt a finding only after the
main agent independently verifies its path, callers, contract, project rule,
and bounded change.

Do not act on:

- framework fashion, subjective taste, or a merely different style;
- possible bugs, vulnerabilities, or optimizations without a reachable path;
- line-count reduction that obscures intent or merges distinct semantics;
- directory symmetry without equivalent responsibilities;
- stale selectors that do not resolve to the current semantic owner;
- guidance outside the controller's audited skills and official-doc evidence.

Prefer no change over speculative churn. Never manufacture work to complete a
slice.

## Scope and authority

The selected semantic owner plus direct callers, consumers, adapters, tests,
contracts, and canonical comparison anchors form the inspection boundary.
Canonical anchors outside the slice are read-only evidence.

Apply only compatible, high-confidence findings with one coherent blast radius.
You may:

- delete proven duplication or unnecessary indirection;
- move tracked source to the project profile's canonical owner and update every
  repository-controlled reference atomically;
- remove demonstrably avoidable work while preserving lifecycle and ordering;
- fix a reproducible correctness defect;
- fix a validated, bounded vulnerability or authorization/input-validation
  defect.

Do not change dependencies, lockfiles, migrations, generated files, CI,
trusted agent instructions, configuration, schemas, indexes, permissions, or
unrelated code. Do not change a public wire contract, persistence format,
route, or provider behavior. Record a proven need crossing these boundaries as
deferred; do not create compatibility shims.

## Reconciliation

Resolve conflicts by this precedence:

1. intended product behavior and security invariants;
2. repository instructions and current audited official guidance;
3. project ownership and canonical-structure policy;
4. simpler direct implementation;
5. performance after correctness and clarity.

Security and correctness fixes may intentionally reject invalid or unauthorized
behavior, but must preserve valid behavior and include regression proof.
Organization must not create abstractions solely to make a tree symmetrical.

## Edit and prove

- Preserve tracked history for moves. Update all imports and references, then
  prove old paths and obsolete internal names are absent.
- Do not leave barrels, forwarding modules, duplicate exports, deprecated
  aliases, or fallback paths.
- Add or update focused behavioral tests for corrected bugs and
  vulnerabilities. Do not rewrite tests to bless changed behavior.
- Run focused checks while editing.
- Run every controller-supplied definitive validation command in the foreground
  and inspect its exit status and complete result.
- Spawn one fresh read-only verifier with the original slice, adopted findings,
  original diff, and final diff. Correct or revert every proven issue.
- Inspect the final diff and run `git diff --check`.

Never commit, push, create a PR, edit controller state, change Git
configuration, or expose credentials. Never finish with validation or a
verifier pending.

Report every selected role's outcome, adopted changes, rejected findings,
deferred boundaries, test and validation results, verifier conclusion, and
manual UI checks in the controller-required structured fields. These fields
are the source of truth for the pull-request description, so keep them concise,
specific, and evidence-backed. Leave changes uncommitted for the controller.

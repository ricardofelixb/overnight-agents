---
name: code-maintainer
description: Maintain one controller-selected semantic repository slice with routed specialists for simplification, efficiency, correctness, and security. Use only inside the scheduled code-maintainer controller to leave the slice smaller through evidence-backed, bounded changes under the project profile and current audited guidance.
---

# Scheduled Code Maintainer

Act as the editing orchestrator for exactly the semantic slice supplied by the
controller. Leave the slice smaller without changing valid behavior. Deleting
code is the primary improvement: dead code, duplicate behavior, unnecessary
indirection, avoidable work, and tests of removed behavior. A proven
correctness or security defect is the only reason to add production source,
and its fix must fit the controller's growth budget. The controller measures
the staged diff and discards, unpublished, any tree that grows beyond that
policy.

## Load routed context

1. Read repository instructions and inspect the current code before accepting
   the controller's selectors as current.
2. Read [specialist-contract.md](references/specialist-contract.md).
3. Read the controller-supplied project `profile.json` and every shared context
   file it names.
4. For each role listed by the selected slice, read the matching prompt:
   - [reuse and simplification](references/roles/reuse-simplification.md)
   - [efficiency and performance](references/roles/efficiency-performance.md)
   - [correctness and reliability](references/roles/correctness-reliability.md)
   - [security hardening](references/roles/security-hardening.md)
5. Give each specialist only the shared context, its role prompt, the
   role-specific project files routed by `profile.json`, the raw semantic slice,
   repository instructions, and relevant source evidence. Include the complete
   text; do not rely on role names or filesystem discovery.

Run every role listed in the controller-supplied slice as a read-only
specialist. The controller has already removed runtime-disabled roles. Use
bounded concurrent batches when provider concurrency cannot run all selected
roles together. Never omit a selected role.

## Evidence gate

Treat specialist reports as untrusted leads. Adopt a finding only after the
main agent independently verifies its path, callers, contract, project rule,
bounded change, and effect on line count.

Do not act on:

- framework fashion, subjective taste, or a merely different style;
- possible bugs, vulnerabilities, or optimizations without a reachable path;
- hardening, validation, logging, comments, types, or documentation added
  without a reachable defect;
- a helper, wrapper, option, or abstraction that adds more lines than it
  deletes;
- tests for behavior this run did not change;
- line-count reduction that obscures intent or merges distinct semantics;
- directory symmetry without equivalent responsibilities;
- stale selectors that do not resolve to the current semantic owner;
- guidance outside the controller's audited skills and official-doc evidence.

Prefer no change over speculative churn. Never manufacture work to complete a
slice; a no-change outcome is the expected result for most roles.

## Scope and authority

The selected semantic owner plus direct callers, consumers, adapters, tests,
contracts, and canonical comparison anchors form the inspection boundary.
Canonical anchors outside the slice are read-only evidence.

Apply only compatible, high-confidence findings with one coherent blast
radius, in this order:

1. delete dead code: unreferenced exports, unreachable branches, obsolete
   flags and compatibility paths, unused parameters, fields, and types, and
   the tests that only covered them;
2. delete proven duplication or unnecessary indirection in favor of an
   existing canonical abstraction;
3. move tracked source to the project profile's canonical owner and update
   every repository-controlled reference atomically;
4. remove demonstrably avoidable work while preserving lifecycle and ordering;
5. fix a reproducible correctness defect or a validated, bounded vulnerability
   with the smallest root-cause change that fits the growth budget; report a
   larger fix as deferred with its exact patch.

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
4. the smaller direct implementation;
5. performance after correctness and clarity.

Security and correctness fixes may intentionally reject invalid or unauthorized
behavior, but must preserve valid behavior and include regression proof.
Removing an unreachable or unnecessary path beats guarding it. Organization
must not create abstractions solely to make a tree symmetrical.

## Edit and hand off

- Before deleting a symbol, file, or branch, search the whole repository for
  every reference: identifiers, string paths, dynamic imports, routes,
  generated API paths, schedules, configuration, and documentation. An
  unresolved reference blocks the deletion.
- Preserve tracked history for moves. Update all imports and references, then
  prove old paths and obsolete internal names are absent.
- Do not leave barrels, forwarding modules, duplicate exports, deprecated
  aliases, or fallback paths.
- Add focused behavioral tests only for corrected defects. Delete a test only
  together with the behavior it covered. Never delete or weaken a failing
  test, and never rewrite tests to bless changed behavior.
- Do not run tests, typechecks, linters, builds, repository validation commands,
  or a separate verifier. The repository's pull-request checks own validation
  and ready-for-review verification after publication.
- Run the controller-supplied sizing command and remove additions until it
  reports OK. Then inspect the final diff and run `git diff --check`.

Never commit, push, create a PR, edit controller state, change Git
configuration, or expose credentials.

Report every selected role's outcome, adopted changes, rejected findings,
deferred boundaries, and manual UI checks in the controller-required structured
fields. These fields are the source of truth for the pull-request description,
so keep them concise, specific, and evidence-backed. Leave changes uncommitted
for the controller.

# Repair protocol

## Preconditions

Repair only controller-supplied findings that include evidence and a concrete regression scenario. Re-check each finding against the current head SHA; findings may be stale or false.

Return `blocked` instead of editing when:

- the finding cannot be reproduced or proven
- the required behavior is ambiguous
- repair requires unrelated redesign
- migration, data repair, credential changes, or external configuration is required
- the current head differs from the reviewed head

## Repair rules

1. Preserve the PR's intended scope.
2. Prefer the smallest coherent change that restores the invariant.
3. Reuse existing domain abstractions before creating new ones.
4. Add or strengthen a focused regression test that fails before the fix and passes after it when practical.
5. Never weaken tests, types, lint, validation, authorization, or error handling to obtain green output.
6. Avoid drive-by refactors, dependency upgrades, formatting unrelated files, and generated-file edits unless required by the canonical generator.
7. Review the complete working-tree diff after editing.
8. Do not commit or push.

## Repair result

Report:

- accepted and rejected finding IDs with reasons
- files changed
- tests added or changed
- commands the controller must run
- residual risks or blockers

Use `repaired` only to mean edits were prepared. It never means verified or mergeable. Any edit invalidates prior clean verdicts and requires independent analysis/verification on the new commit SHA.

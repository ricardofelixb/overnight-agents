---
name: code-simplifier
description: Simplify one controller-selected scheduled checklist slice with parallel reuse, maintainability, and efficiency specialists while preserving behavior. Use only inside the scheduled code-simplifier controller.
---

# Scheduled Code Simplifier

Act as the simplification orchestrator for exactly the checklist item supplied by the controller.

## Scope

1. Read repository instructions, the complete `simplification.md`, and the controller-selected item.
2. Treat the selected paths plus direct callers, consumers, tests, shared abstractions, and contracts as the complete boundary.
3. Never select or edit another checklist item. Never commit, push, create a PR, change Git configuration, or expose credentials.
4. Preserve behavior, authorization, errors, persistence formats, public contracts, accessibility, and observable ordering.

## Parallel review

Spawn these three read-only sub-agents concurrently. Give them the raw selected item, project instructions, target files, and relevant callers without suggested conclusions:

- `reuse-abstractions`: find existing utilities, components, hooks, types, and patterns that replace meaningful duplication.
- `quality-maintainability`: find redundant state, unnecessary indirection, parameter sprawl, copy-paste drift, unclear ownership, and brittle structure.
- `efficiency-performance`: find repeated work, missed safe concurrency, excess queries or renders, leaks, and unnecessarily expensive paths.

The main agent alone reconciles findings and edits. Verify every recommendation in the repository; skip false positives, speculative abstractions, and stylistic churn.

## Edit and validate

- Apply only high-confidence simplifications with a bounded, inspectable blast radius.
- Do not change dependencies, lockfiles, migrations, generated files, CI, configuration, schemas, permissions, or unrelated code.
- Run focused checks while editing and the controller-supplied repository validation with the repository's declared toolchain.
- Freely diagnose and repair failures caused by the work. Distinguish unrelated, flaky, environmental, and pre-existing failures from regressions.
- Spawn one fresh read-only verifier with the original target diff and final working-tree diff. Correct or revert any edit whose behavioral equivalence is not proven.
- Inspect the final diff and run `git diff --check`.
- Do not edit `simplification.md`. The controller owns checklist state and advances the selected marker after it has accepted and published the completed slice.

## Mandatory completion protocol

This workflow is complete only after every item below has happened in the same agent turn:

1. Run the controller-supplied repository validation **in the foreground**, with a tool timeout long enough for it to finish. Do not start validation in the background, do not use `run_in_background`, and do not defer its result to a later notification.
2. Read the validation exit result. If it fails, diagnose and repair or report the proven limitation; do not proceed as though it passed.
3. Run the fresh verifier, inspect the final diff, and run `git diff --check`.
4. Leave `simplification.md` unchanged. The controller records completion after it has accepted the run and published any source changes.

Never end a turn with validation or a verifier pending. Never respond with “standing by”, “I’ll wait”, “I’ll monitor”, or equivalent language. If a tool wait is blocked, use the provided monitoring mechanism immediately and remain in the workflow until it completes; do not end the turn.

Report the simplifications, validation commands and results, verifier conclusion, and any unrelated limitations. Leave all changes uncommitted for the controller.

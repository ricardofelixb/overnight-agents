# Merge gates

The model never executes these gates. Use them to decide whether evidence is sufficient for `clean`.

The controller must refuse merge unless all are true:

1. The PR is open, non-draft, trusted, and targets the configured base branch.
2. The head branch matches an allowed pattern and belongs to the configured repository owner.
3. Every independent review result is schema-valid and `clean` for the same base and head SHA.
4. Required documentation evidence is current under controller policy.
5. Project-defined full validation exits zero and required output markers are present.
6. Required GitHub checks pass; none are pending, missing, cancelled, or skipped when required.
7. The PR remains mergeable and no unresolved blocking review state exists.
8. The current PR head still equals the reviewed head immediately before merge.
9. Repair, if any, was committed by the controller and followed by fresh independent review passes.
10. The merge command uses squash strategy, exact head matching, and configured branch deletion.

Any failure is fail-closed. The controller records the reason and leaves the PR open.

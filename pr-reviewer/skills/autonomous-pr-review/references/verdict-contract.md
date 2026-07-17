# Verdict contract

The controller supplies a JSON Schema. Return exactly one conforming JSON object.

## Semantic rules

- Echo full 40-character `reviewed_base_sha` and `reviewed_head_sha`.
- Use a stable finding ID derived from the invariant and location, not array position.
- Keep line ranges tight and within the reviewed head revision.
- Include documentation evidence only when actually read in the run.
- List every changed file in either `changed_files_reviewed` or `changed_files_unreviewed`.
- `clean` requires an empty findings array, empty blockers, empty unreviewed files, and all behavioral contracts marked `preserved`.
- `fixable` requires at least one finding with `auto_fix_safe: true` and no blocker that prevents repair.
- `blocked` requires at least one precise blocking reason.
- Never encode merge authorization, shell commands, or GitHub credentials in free-form fields.

## Contract statuses

- `preserved`: evidence supports unchanged or intentionally changed behavior.
- `regressed`: a concrete finding demonstrates violation.
- `uncertain`: required evidence is missing; verdict must be `blocked` unless another finding makes it `fixable` and the uncertainty does not affect repair safety.

## Documentation evidence

Record:

- provider
- installed version when known
- skill name and source revision when known
- official URL
- retrieval timestamp or approved cache timestamp
- compatibility conclusion

Do not state “latest” without a retrieval timestamp.

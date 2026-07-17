# Review protocol

## Contents

1. Scope and behavior map
2. Correctness passes
3. Finding standard
4. Clean standard

## 1. Scope and behavior map

Start with `git diff --stat`, `git diff --name-status`, and the exact base-to-head diff. Record:

- changed public and internal behavior
- added, removed, renamed, and moved symbols
- affected entrypoints, callers, consumers, schemas, and serialized contracts
- persistence, authorization, cache, concurrency, rendering, and error-handling boundaries
- tests that claim to cover the behavior

Review deletions as behavior changes. Search for stale imports, dynamic references, route registrations, documentation contracts, generated APIs, feature flags, and configuration consumers.

## 2. Correctness passes

Apply every relevant pass. The controller may assign an emphasis lens, but visible defects outside that lens still count.

### Behavioral equivalence

- Compare old and new behavior for normal, boundary, empty, null, malformed, retry, and failure inputs.
- Trace changed return shapes, thrown errors, ordering, timing, side effects, and default values into direct consumers.
- Check that extracted or shared code preserves context, lifetime, and ownership.
- Look for behavior accidentally broadened or narrowed by simplification.

### Data and concurrency

- Inspect transaction boundaries, partial failure, retries, idempotency, race windows, ordering, batching, pagination, and unbounded reads/writes.
- Verify indexes, filters, cache keys, invalidation, subscriptions, and consistency expectations.
- Check that parallelization does not violate dependency or transaction ordering.

### Authentication and authorization

- Trace identity from trusted source to every protected read/write.
- Check cross-tenant and cross-organization isolation, roles, permissions, object ownership, token audience/issuer, session lifecycle, and internal/public function exposure.
- Require negative tests for meaningful authorization boundaries.

### Contracts and compatibility

- Inspect database schemas, API/MCP/OpenAPI shapes, event payloads, environment variables, routes, generated types, storage formats, and backwards compatibility.
- Verify all adapters reuse canonical business logic rather than drifting copies.
- Check migration and rollout compatibility when old and new versions may coexist.

### React and client behavior

- Preserve Server/Client Component boundaries, hook ordering, effect dependencies, hydration, loading/error/empty states, transitions, focus, accessibility, and state ownership.
- Check stale closures, redundant derived state, unstable provider values, request waterfalls, repeated subscriptions, bundle regressions, and accidental client expansion.

### Resource and operational safety

- Check memory growth, listener cleanup, timers, file handles, network retries, timeouts, rate limits, logs, PII, secrets, and failure observability.
- Check that config, CI, deployment, or validation edits do not remove safeguards.

### Tests

- Read the assertions, not just filenames.
- Confirm tests execute production entrypoints and fail for the regression scenario.
- Check success, failure, boundary, authorization, and contract paths appropriate to the change.
- Flag mocks that reproduce implementation logic or bypass the changed behavior.

## 3. Finding standard

Report a finding only when all are present:

- exact file and tight line range
- violated invariant or expected behavior
- concrete triggering input/state/caller
- observable impact
- evidence from code, contract, test, or current official documentation
- focused repair direction and required validation

Use severities:

- `P0`: imminent catastrophic loss, compromise, or broad outage
- `P1`: likely security, data-integrity, authorization, or major functional regression
- `P2`: real correctness or material performance/reliability regression with bounded impact
- `P3`: low-impact defect worth fixing before merge

Do not report preferences, speculative future cleanup, formatting, or style already enforced mechanically.

## 4. Clean standard

Return `clean` only if:

- every changed file and meaningful deletion was inspected
- direct callers/consumers needed for behavioral proof were inspected
- all relevant contracts are recorded as preserved
- provider guidance and current official docs were consulted when applicable
- tests were assessed for regression sensitivity
- no candidate finding survived the concrete-scenario test
- no uncertainty or missing evidence remains

List coverage truthfully in the structured output. An incomplete pass is `blocked`, not `clean`.

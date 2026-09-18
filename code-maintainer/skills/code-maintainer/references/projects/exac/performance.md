# Exac performance rules

For Convex, read the current workspace
`convex/_generated/ai/guidelines.md` before analysis. Use the audited
`convex-optimize` skill only for a concrete Convex mechanism.

Assess query bounds, index use, documents and bytes read, subscription
invalidation, transaction duration, write contention, action/query boundaries,
and repeated domain reads. Preserve reactive semantics and authorization.
Schema, index, migration, aggregate, and document-layout changes are deferred.

For React and Next.js, use the audited `vercel-react-best-practices` skill only
for the relevant mechanism: waterfalls, bundle loading, server work, client
data, rerenders, rendering, or JavaScript work. Preserve loading, error,
authentication transition, provider, cache, and cleanup behavior.

For external providers, count network round trips and serial dependencies while
preserving idempotency, rate limits, retries, and provider error semantics.
Never claim an optimization from line count alone.

Convex function calls, action compute, database bandwidth, and storage are
billed, as are calls to WorkOS and any other provider. Rank duplicate
queries, over-broad reads and subscriptions, polling, and repeated provider
calls on hot or scheduled paths as cost findings, not only latency findings.

WorkOS calls are not ordinary fetches. Preserve session refresh, identity and
organization synchronization, pagination completeness, rate limits, retries,
and provider error semantics. Use current audited WorkOS guidance before
changing batching, caching, ordering, or call ownership.

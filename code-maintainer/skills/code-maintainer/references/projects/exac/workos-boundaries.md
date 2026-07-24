# Exac WorkOS boundaries

WorkOS is a core Exac identity, organization, session, permission, widget, and
API-key integration. Never treat WorkOS code as incidental provider plumbing.

Relevant owners include `convex/auth/`, `convex/auth.config.ts`,
`convex/lib/workos.ts`, `convex/lib/workosWidgets.ts`,
`convex/organizations/actions/workos.ts`, `convex/members/`,
`convex/api/auth.ts`, `src/lib/auth.ts`, the WorkOS contexts and hooks, and
their direct tests and adapters.

For organization work, preserve the public provider vocabulary and module
contracts while applying Exac's canonical internal naming and ownership rules.
Do not move an authentication callback, session owner, organization lifecycle,
webhook, or provider adapter merely for tree symmetry.

For correctness and security, trace the complete boundary:

- provider identity to local user identity;
- session and token claims to selected organization and permissions;
- organization and membership create, update, invitation, removal, and delete
  ordering across WorkOS and Convex;
- widget token subject, session, and organization checks;
- webhook signature, timestamp/replay, idempotency, and event ordering;
- API-key validation, capability mapping, and tenant scope;
- redirects, callbacks, errors, logs, and safe client projections.

For performance, count provider round trips and pagination, but preserve session
refresh, complete lifecycle operations, rate-limit behavior, retries,
idempotency, and provider error semantics. Never cache identity, membership,
organization, permission, or session data beyond the repository's proven
invalidation boundary.

Consult only the controller-supplied current hashed `workos` or
`workos-widgets` skill and allowlisted official WorkOS documentation for a
concrete mechanism. Preserve external WorkOS contracts. Defer changes to token,
session, webhook, callback, organization, or user lifecycle semantics unless
the contract and regression proof are complete.

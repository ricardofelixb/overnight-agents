# Exac core invariants

Exac is a Next.js, React, TypeScript, and Convex multi-tenant Mexican invoicing
system with WorkOS as a core identity and organization integration.
Organization scope, authorization, sessions, safe projections, monetary and
tax precision, provider behavior, and client-facing Spanish surfaces are
behavior.

Read the workspace `AGENTS.md` and `CLAUDE.md` first. They override this
profile.

Preserve:

- identity source, organization selection, permissions, and cross-organization
  isolation;
- public wire formats, Convex function namespaces, persistence formats, routes,
  observable ordering, and error behavior;
- monetary units, rounding, fiscal identifiers, SAT/CFDI semantics, provider
  idempotency, and transaction boundaries;
- client-facing Spanish copy, accessibility text, notifications, validation
  messages, and intentional Spanish route segments;
- React auth-transition query skips, provider ownership, cache behavior,
  reactivity, cancellation, and cleanup.

Tests and current project documentation are contract evidence. A simpler or
shorter implementation is better only when these invariants remain explicit.

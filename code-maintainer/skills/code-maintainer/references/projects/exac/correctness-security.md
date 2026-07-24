# Exac correctness and security rules

Every organization-visible operation derives authenticated identity on the
server, resolves organization scope through the canonical owner, enforces the
required permission, and returns a safe projection. Never accept a user or
organization identifier as authorization proof.

WorkOS authentication, users, organizations, sessions, permissions, widgets,
webhooks, and API-key validation are core trust boundaries. Verify token
subject, session, organization, issuer/audience where applicable, webhook
authenticity and replay behavior, and local/WorkOS lifecycle ordering against
current audited WorkOS guidance. Treat changes to these boundaries as
higher-risk provider-contract changes.

Check:

- cross-organization object access and enumeration;
- adapter paths bypassing domain authorization, validation, or projection;
- internal functions accidentally exposed as public Convex functions;
- money, rounding, tax, invoice status, payment allocation, and idempotency
  edge cases;
- unsafe external URLs, redirects, webhook trust, replay, file handling, XML or
  document parsing, and error leakage where reachable;
- transaction splits, retries, partial writes, and action/mutation ordering;
- API, MCP, programmatic, OpenAPI, and localized documentation parity when a
  canonical operation contract is involved.

Backend regression tests live under `tests/<domain>/`, import the real schema,
use `import.meta.glob('../../convex/**/*.ts')`, and exercise functions through
`convex/_generated/api` with `convex-test`. Cover success, validation or
`ConvexError`, and cross-organization denial. Do not reimplement production
logic in a test.

Do not expose internal jobs, migrations, provider plumbing, secrets, or
storage-specific fields. Security fixes preserve authorized behavior and must
prove the denied path.

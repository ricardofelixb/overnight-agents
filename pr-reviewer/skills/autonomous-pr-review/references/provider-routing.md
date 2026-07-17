# Provider and framework routing

Use global provider skills as workflow routers. Use project rules, installed package versions, generated guidance, exact package types, and current official documentation as technical evidence.

## Routing order

For each provider or framework touched by the diff:

1. Identify exact installed versions from the lockfile or package metadata.
2. Read applicable project-generated guidance and provider-specific project rules.
3. Invoke/read the matching globally installed official skill.
4. Fetch current official documentation from controller-allowlisted domains.
5. Resolve version differences. Do not recommend a latest-doc API that the installed version does not support.
6. Record skill name/source version, documentation URL, retrieval time, installed version, and compatibility conclusion.

Return `blocked` when required documentation is unavailable and no controller-approved fresh cache exists.

## Known routing signals

### Convex

Signals include `convex/`, Convex imports, generated Convex APIs, Convex configuration, or a Convex dependency change.

- Read `convex/_generated/ai/guidelines.md` when present.
- Read applicable Convex skills, especially best-practice, security, schema, migration, auth, and performance guidance selected by the diff.
- Consult `https://docs.convex.dev/llms.txt` and targeted official pages.
- Preserve authentication, authorization, validators, indexes, visibility, transaction boundaries, pagination, scheduling, and generated contracts.

### React, Next.js, and Vercel

Signals include React/Next dependencies, `.tsx` files, app/pages routes, server actions, route handlers, hooks, contexts, or Vercel configuration.

- Read the official global `vercel-react-best-practices` skill and only the relevant rule files.
- Consult current React, Next.js, or Vercel official documentation for changed APIs.
- Treat performance rules as conditional: preserve semantics and project architecture before optimizing.

### WorkOS

Signals include WorkOS/AuthKit imports, auth/session code, organization membership, RBAC, SSO, webhooks, widgets, tokens, or WorkOS environment configuration.

- Read the official global `workos` router skill and its exact topic reference.
- Read `workos-widgets` only for widget surfaces.
- Consult `https://workos.com/docs/llms.txt`, the exact SDK documentation, and the installed SDK/component types.
- Verify session, cookie, PKCE/CSRF, issuer/audience, organization context, role/permission, cross-tenant, webhook signature, and token-refresh behavior as applicable.

## Unknown providers

Detect provider ownership from imports, manifests, generated files, and configuration. Prefer official vendor documentation and official skills. Do not use community guidance as the sole basis for a blocking finding or autonomous repair.

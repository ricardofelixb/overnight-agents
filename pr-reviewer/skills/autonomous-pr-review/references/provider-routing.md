# Provider and framework routing

Use global provider skills as workflow routers. The controller manifests are allowlisted candidate catalogs; presence in a catalog is not an instruction to read every entry. Use project rules, installed package versions, generated guidance, exact package types, and current official documentation as technical evidence.

## Routing order

First map the concrete behavior and question under review. If resolving that question depends on provider or framework semantics:

1. Identify the exact installed version from package metadata or the lockfile.
2. Read applicable project-generated guidance and provider-specific project rules.
3. Read the provider router skill, then only the topic skill or rule files triggered by the concrete question.
4. Read only the controller-cached official document needed to verify the relevant API or invariant.
5. Resolve version differences. Do not recommend a latest-doc API that the installed version does not support.
6. Record only the skill and documentation actually used.

Do not consult provider material merely because a provider name, import, or file extension appears in the diff. Repository evidence may be sufficient for provider-independent behavior. Return `blocked` when provider evidence is necessary for a material conclusion but the applicable skill or official documentation is unavailable.

## Known routing signals

### Convex

Signals include `convex/`, Convex imports, generated Convex APIs, Convex configuration, or a Convex dependency change.

- Read `convex/_generated/ai/guidelines.md` when present.
- Start with the Convex router skill and select auth, migration, performance, component, or other topic skills only when the reviewed behavior requires them.
- Consult `https://docs.convex.dev/llms.txt` or a targeted official page only for the specific API or invariant being verified.
- Preserve authentication, authorization, validators, indexes, visibility, transaction boundaries, pagination, scheduling, and generated contracts.

### React, Next.js, and Vercel

Signals include React/Next dependencies, `.tsx` files, app/pages routes, server actions, route handlers, hooks, contexts, or Vercel configuration.

- Read the official global `vercel-react-best-practices` skill only for React/Next behavior or performance questions, then open only relevant rule files.
- Consult current React, Next.js, or Vercel official documentation for changed APIs.
- Treat performance rules as conditional: preserve semantics and project architecture before optimizing.

### WorkOS

Signals include WorkOS/AuthKit imports, auth/session code, organization membership, RBAC, SSO, webhooks, widgets, tokens, or WorkOS environment configuration.

- Read the official global `workos` router skill only when WorkOS behavior is material, then open its exact topic reference.
- Read `workos-widgets` only for widget surfaces.
- Consult `https://workos.com/docs/llms.txt`, the exact SDK documentation, and the installed SDK/component types.
- Verify session, cookie, PKCE/CSRF, issuer/audience, organization context, role/permission, cross-tenant, webhook signature, and token-refresh behavior as applicable.

### Stripe

- Consult `https://docs.stripe.com/llms.txt`.

## Unknown providers

Detect provider ownership from imports, manifests, generated files, and configuration. Prefer official vendor documentation and official skills. Do not use community guidance as the sole basis for a blocking finding or autonomous repair.

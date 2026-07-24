# Exac authoritative guidance

Use evidence in this order:

1. current workspace `AGENTS.md`, `CLAUDE.md`, package versions, types, tests,
   and domain contracts;
2. current workspace `convex/_generated/ai/guidelines.md` for Convex work;
3. the controller-supplied audited context-evidence JSON;
4. the exact hashed skill release named by that evidence for a concrete
   mechanism;
5. the exact official-document cache entry named by its manifest.

The controller blocks work when required skill releases, Convex AI files, or
official documentation are stale or fail integrity checks. Do not substitute
model memory, blogs, search snippets, or an unverified installed skill.

Load progressively:

- Convex mechanism: the narrow audited Convex skill named by the evidence.
- React/Next.js mechanism: audited `vercel-react-best-practices`.
- WorkOS mechanism: audited `workos` or `workos-widgets`.
- Security mechanism: the narrow installed Codex Security skill, while keeping
  repository reachability and tests as primary proof.

Read `convex/api/README.md` or `convex/mcp/README.md` when those transports are
in scope. Read public API/OpenAPI documentation tests when a programmatic
contract is in scope.

Run all `pnpm`, Convex, and React Doctor commands outside the sandbox as
required by repository instructions. The definitive Exac gate is
`pnpm run validate`: zero errors, zero warnings, React Doctor 100, all tests
green, and `Convex functions ready`.

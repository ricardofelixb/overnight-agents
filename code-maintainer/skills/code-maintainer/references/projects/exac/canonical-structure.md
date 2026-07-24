# Exac canonical source structure

This document is normative for maintainability findings. Equivalent
responsibilities use the same location and naming pattern across the codebase.
Do not copy responsibilities a domain does not have.

## Vertical identity

Use one English internal domain across correlated layers while preserving an
intentional Spanish client route:

```text
src/app/(dashboard)/cuentas-por-pagar/   # thin route
src/components/payables/                 # frontend owner
convex/payables/                         # backend owner
tests/payables/                          # behavioral mirror
```

Frontend multiword domain directories use English `kebab-case`, such as
`public-portals` and `account-ledger`. Convex multiword directories use
`lowerCamelCase`, such as `publicPortals`, `paymentPortal`, and `fiscalSeries`,
because paths form generated API properties. Intentional Spanish route segments
remain Spanish.

## Frontend domains

`src/components/payables/` is the compact large-domain reference:

```text
payables/
├── PayablesPageView.tsx
├── PayablePaymentAction.tsx
├── comboboxes/
├── details/
├── dialogs/
├── forms/
├── sheets/
├── tables/
└── widgets/
```

`payables` and `receivables` are symmetry anchors for shared accounting roles.
The domain root is an interface. Keep only:

- the primary `<Domain>PageView.tsx`;
- a true cross-role/domain orchestrator;
- a framework-mandated or public entrypoint.

Place role-specific components in the established plural directory. Place a
cohesive feature with multiple roles in a semantic subdomain. Do not accumulate
root forms, tables, dialogs, hooks, types, constants, fixtures, or pure helpers.
Three or more root files sharing a declared role are strong evidence of a
missing group, not an independent rule.

Do not create empty folders or decorative one-file folders. A one-file
semantic subdomain is valid only for a real ownership boundary. Prefer the
actual role or semantic owner over generic `helpers/`, `utils/`, or `misc/`.

## Convex domains

A small domain may remain flat when every root module is a distinct file-routed
entry or cohesive responsibility. A large domain follows the
`convex/sat/declarations/` pattern:

```text
declarations/
├── actions/
├── mutations/
├── queries/
├── lib/
├── contract.ts
├── validators.ts
└── tableValidators.ts
```

Use role folders once the role has multiple cohesive modules. Use `contracts/`
for multiple contracts and semantic subdomains for independently owned
workflows. `lib/` is bounded domain internals, never a miscellaneous bucket.

Convex file paths are contract-sensitive. Inventory `api.*`, `internal.*`,
HTTP, MCP, programmatic, tests, schedules, and generated references before a
move. Defer a move changing a public namespace, framework discovery, schema,
index, migration, or external consumer. Never hand-edit generated files or
retain the old path with a forwarder.

## Filenames

- React component: `PascalCase.tsx`, matching the primary export exactly.
- Hook: `useFeatureName.ts`, or `.tsx` only when the file contains JSX.
- Pure TypeScript module: descriptive `lowerCamelCase.ts`, such as
  `payableMovementDisplay.ts`.
- Frontend role folder: plural English `kebab-case` noun.
- Convex module and directory: English `lowerCamelCase`.
- Test: match the subject, such as `PayablesPageView.test.tsx` or
  `settlementTerms.test.ts`.
- Framework-required names such as `page.tsx`, `layout.tsx`, `route.ts`, and
  Convex `validators.ts` remain exact.

Do not introduce internal `snake_case`, mixed casing, Spanish internal names,
root `utils.ts`/`helpers.ts`, `index.ts` barrels, aliases, or forwarding files.
Preserve external snake-case fields only when an official wire or provider
contract requires them.

## Atomic correction

```text
resolve semantic owner
→ inventory every reference and contract
→ preserve the tracked move
→ update all repository-controlled consumers
→ remove the old path and obsolete internal name
→ search for stale references
→ run focused behavior tests and definitive validation
```

If this requires a compatibility layer or unknown external consumer, defer it.

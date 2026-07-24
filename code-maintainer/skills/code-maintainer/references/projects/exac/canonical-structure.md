# Exac canonical source structure

This document is normative for maintainability findings. Equivalent
responsibilities use the same location and naming pattern across the codebase.
Do not copy responsibilities a domain does not have.

## Vertical identity

Use one English internal domain across correlated layers while preserving an
intentional Spanish client route:

```text
src/app/(dashboard)/<spanish-route>/     # framework route
src/components/<domain>/                 # frontend owner
convex/<domain>/                         # backend owner
tests/<domain>/                          # behavioral mirror
```

Frontend multiword domain directories use English `kebab-case`, such as
`public-portals` and `account-ledger`. Convex multiword directories use
`lowerCamelCase`, such as `publicPortals`, `paymentPortal`, and `fiscalSeries`,
because paths form generated API properties. Intentional Spanish route segments
remain Spanish.

Routes are framework entrypoints, not compatibility layers. A route may import
its canonical page owner, but do not create or retain a legacy route, proxy
route, alternate route segment, or thin forwarding module to preserve an old
internal location. If changing a public URL or external API namespace is
required, defer the move rather than adding a compatibility shim.

## Frontend domains

The following are normative templates, not source-tree anchors. Apply them
even when no existing domain currently matches them. A domain may be rooted
directly under `src/components/`, or a category may group several independent
domains:

```text
src/components/<domain>/
├── DomainPageView.tsx
├── comboboxes/
│   └── DomainAccountCombobox.tsx
├── details/
│   └── DomainDetails.tsx
├── dialogs/
│   └── RemoveDomainDialog.tsx
├── forms/
│   └── DomainForm.tsx
├── sheets/
│   └── RecordDomainPaymentSheet.tsx
├── tables/
│   └── DomainsTable.tsx
└── widgets/
    └── DomainSummary.tsx
```

```text
src/components/<category>/
├── <first-domain>/                 # complete domain template above
└── <second-domain>/                # complete domain template above
```

A category only groups multiple independent domains. A cohesive workflow within
one domain is a semantic subdomain, not a category:

```text
src/components/<domain>/<subdomain>/
├── dialogs/
├── forms/
├── hooks/
├── lib/
└── WorkflowView.tsx
```

The domain root is an interface, not a component catch-all. Keep only:

- the primary `<Domain>PageView.tsx`;
- a true cross-role/domain orchestrator;
- a framework-mandated or public entrypoint.

Place role-specific components in the established plural directory. Place a
cohesive feature with multiple roles in a semantic subdomain. Do not accumulate
root forms, tables, dialogs, hooks, types, constants, fixtures, or pure helpers.
Three or more root files sharing a declared role are strong evidence of a
missing group, not an independent rule. Every root module must satisfy one of
the three root allowances above; otherwise move it to its role folder or
semantic subdomain. Do not leave orphaned domain modules at the root.

Do not create empty folders or decorative one-file folders. A one-file
semantic subdomain is valid only for a real ownership boundary. `lib/` is for
domain-private, non-UI modules shared across role folders. Keep a helper used
by one role with that role, and name folders for their actual responsibility:
`sections/`, `hooks/`, `validation/`, or `payloads/`, for example. Do not use
generic `helpers/`, `utils/`, or `misc/`.

## Convex domains

Every Convex domain is organized into semantic subfolders. A backend domain
may be either `convex/<domain>/` or, when it owns a cohesive workflow,
`convex/<domain>/<subdomain>/`. The final ownership directory—`<domain>` in
the first form or `<subdomain>` in the second—is the domain root for this
policy.

Flat domains are not permitted, regardless of size: do not place application
modules directly in the final ownership directory. A single-function domain still
uses the role folder for that function.

The following is the normative Convex template, not a source-tree anchor.
Apply it even when no existing domain currently matches it, omitting only role
folders the domain does not need:

```text
convex/<domain>/<subdomain>/        # or convex/<domain>/
├── actions/
│   ├── retrieval.ts                # exports download and full actions
│   └── polling.ts                  # exports process action
├── mutations/
│   └── declarations.ts             # exports save mutation
├── queries/
│   └── dashboard.ts                # exports list and summary queries
├── contracts/
│   └── declaration.ts              # argument/result contract types
├── validators/
│   └── declaration.ts              # reusable and table-document validators
└── lib/
    └── parseDeclaration.ts         # domain-private pure/helper logic
```

Public Convex functions belong only in `actions/`, `mutations/`, or
`queries/`. Within each role folder, group related public functions in a named
workflow module rather than creating one small file per operation. For example,
`actions/retrieval.ts` exports `download` and `full`, producing
`api.<domain>.<subdomain>.actions.retrieval.download` and
`api.<domain>.<subdomain>.actions.retrieval.full`.

Keep generated API paths semantic and shallow: domain, optional subdomain, role,
workflow, operation. Do not add a folder level that does not name a durable
public workflow. Expand a workflow module into a same-named directory only
when it needs multiple independently routed modules or tightly owned private
implementation files; keep that directory's name in the API path. Shared
contracts belong in `contracts/`; shared validators belong in `validators/`.
Use a named semantic subdomain beneath the domain root for an independently
owned workflow, then apply this same layout within it. `lib/` is bounded domain
internals, never a miscellaneous bucket.

The only files allowed directly in the final ownership directory are
framework-required entrypoints or configuration files whose required location
cannot be nested. Do not create a root module for convenience, and do not
retain one as a barrel, alias, or forwarding file.

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

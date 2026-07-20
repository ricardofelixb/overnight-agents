# Canonical source structure

## Language boundary

Use Spanish only for client-facing surfaces: visible copy, accessibility text, notifications, validation messages, and intentional route segments such as `/ventas`.

Use English for all internal source vocabulary: directories, filenames, imports, exports, types, variables, comments, test descriptions, configuration keys, and generated function namespaces. Preserve official provider and protocol names.

An expected Spanish string in a test remains Spanish; the test description and identifiers remain English.

## Domain identity

Choose one English product-domain noun and use it consistently across correlated source layers:

| Layer | Sales example |
| --- | --- |
| Client label | `Ventas` |
| Client route | `/ventas` |
| Internal domain | `sales` |
| Convex domain | `convex/sales/` and `api.sales.*` |
| Frontend domain | `src/components/sales/` |
| Route implementation | `SalesPageClient.tsx` |
| Entity nouns | `receipt`, `refund`, `payment` |

Do not replace precise entity nouns with the broader product noun. Use `SalesPageView` for the product surface and `ReceiptDetailsDialog` for the receipt entity.

## Convex patterns

Use lower camel case for multiword Convex domain directories so generated API properties remain dot-addressable. Use the smallest profile that fits the domain.

Small domain:

```text
convex/<domain>/
├── actions.ts
├── mutations.ts
├── queries.ts
├── model.ts
├── validators.ts
├── tableValidators.ts
└── <domain>Contract.ts
```

Large domain:

```text
convex/<domain>/
├── actions/
├── mutations/
├── queries/
├── contracts/
├── <subdomain>/
├── model.ts
├── validators.ts
└── tableValidators.ts
```

Keep public functions in semantically correct modules. Update all repository-controlled callers and generated bindings in the same change. Never retain the old module solely to preserve its generated path.

## Frontend patterns

Use English kebab case for multiword feature directories and plural structural directories.

```text
src/components/<domain>/
├── <Domain>PageView.tsx
├── dialogs/
├── forms/
├── tables/
├── hooks/
├── widgets/
└── <semantic-subdomain>/
```

Do not force empty symmetry. Create a structural directory only when the domain has that role. Prefer semantic subdomains over a generic `helpers/` dumping ground.

Settings sections own their related files:

```text
src/components/settings/sections/
├── certificates/
│   ├── CertificateSettings.tsx
│   ├── CertificateStatusCard.tsx
│   ├── CsdSection.tsx
│   └── FielSection.tsx
├── integrations/
├── organization/
├── portals/
├── sales/
└── synchronization/
```

Visible headings inside those English source files remain Spanish.

## Filename rules

- React component: `PascalCase.tsx`, exactly matching the primary export.
- Hook: `useFeatureName.ts`; use `.tsx` only when the file contains JSX.
- Pure model: `featureModel.ts`.
- Contract: `featureContract.ts`.
- Validation: `featureValidation.ts`; retain canonical Convex `validators.ts` and `tableValidators.ts` entry modules.
- Test: match the subject, such as `sales.test.ts` or `CertificateSettings.test.tsx`.
- Structural directory: plural English noun, such as `tables/`, `forms/`, or `dialogs/`.
- Feature directory: canonical English domain noun.

Never introduce snake_case filenames, Spanish internal names, generic root `utils.ts` or `helpers.ts` dumping grounds, or `index.ts` barrels.

## Clean-refactor rule

Perform an atomic rename:

```text
inventory references
→ move source
→ rename symbols
→ update every caller and adapter
→ regenerate derived bindings
→ remove old paths
→ search for stale vocabulary
→ validate behavior
```

Do not leave forwarding files, re-export barrels, aliases, deprecated names, or fallbacks. If an external consumer makes an atomic rename unsafe, stop and identify that consumer.

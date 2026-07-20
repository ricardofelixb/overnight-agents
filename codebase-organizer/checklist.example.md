# Codebase Organization Checklist

## Vocabulary registry

| Internal domain | Client label | Route | Convex | Frontend | Entity nouns |
| --- | --- | --- | --- | --- | --- |
| `sales` | Ventas | `/ventas` | `convex/sales/` | `src/components/sales/` | receipt, refund, payment |

## Approved vertical slices

- [ ] **sales** — Align the complete sales domain
  - Backend: rename `convex/receipts/` to `convex/sales/` and update generated `api.sales.*` references.
  - Frontend: rename `src/components/receipts/` to `src/components/sales/` and `ReceiptsPageClient` to `SalesPageClient`.
  - Preserve: `/ventas`, all client-facing Spanish text, receipt entity names, schema/table identity, behavior, authorization, and external wire contracts.
  - Remove: old source paths, old generated namespaces, aliases, barrels, forwarding modules, and fallbacks.

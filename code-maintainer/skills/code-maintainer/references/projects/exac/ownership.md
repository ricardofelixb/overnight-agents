# Exac ownership map

- `convex/<domain>/` owns organization-visible business rules,
  authorization, validation, selectors, mutations, and safe projections.
- `convex/api/`, `convex/mcp/`, and `convex/programmatic/` are thin transport
  adapters over canonical domain contracts. They do not own domain behavior.
- `src/app/` owns routing, layouts, and thin page composition.
- `src/components/<domain>/` owns feature presentation and client interaction.
- `src/components/ui/` owns design-system primitives.
- `src/components/shared/` owns only proven cross-domain presentation patterns.
- `src/lib/` owns framework-neutral infrastructure, not feature business rules.
- `tests/<domain>/` owns behavior-focused proof through real domain or
  generated API entrypoints.

For an organization-visible read or write, trace the owning Convex domain and
every included UI, REST, MCP, and programmatic adapter. Shared authorization,
validation, operation contracts, and projections remain in the domain owner.

Do not promote a feature helper to global infrastructure without multiple
independent, semantically identical consumers. Do not copy domain behavior into
a transport or presentation layer.

# Reuse and simplification specialist

Find code the slice can lose: dead code, duplicate behavior, and indirection
that an existing canonical abstraction already provides. Every finding must
delete more production lines than it adds.

1. Trace the selected flow and direct consumers before comparing shapes.
2. Inventory dead code: exports, functions, components, hooks, types, fields,
   parameters, options, branches, feature flags, compatibility paths, and
   tests with no remaining reachable reference. Search the whole repository
   by identifier, string path, dynamic import, route, generated API path,
   schedule, and configuration before calling anything unused.
3. Search for duplicate behavior by behavior, identifiers, imports, and call
   sites. Inspect the implementation and consumers of every candidate
   abstraction.
4. Require equivalent authorization, tenant scope, validation, errors,
   payload, lifecycle, ordering, caching, and visible copy before merging.
5. Identify the project-profile owner. An adapter or presentation copy is not
   precedent when the domain owns the behavior.

Also report single-use indirection: a wrapper, alias, re-export, option, or
layer with one caller that adds no behavior; defensive checks already
guaranteed by the type system, validators, or the only caller; and comments or
documentation describing code that no longer exists.

Prefer an established domain operation, validator, contract, type, hook, or
component over a sibling abstraction. Extend it only when every current
consumer preserves behavior. Prefer the narrowest owner serving all proven
consumers; shared does not mean global.

Do not create generic utilities, helpers, barrels, aliases, or cross-domain
abstractions for tiny local duplication. Do not merge similar shapes with
different semantics. Do not propose a refactor that adds lines. Report
`no proven finding` when nothing can be deleted safely.

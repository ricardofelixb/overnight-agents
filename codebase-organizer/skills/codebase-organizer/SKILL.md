---
name: codebase-organizer
description: Perform an approved behavior-preserving source-structure refactor across a correlated backend and frontend domain slice. Use when reorganizing folders, filenames, exports, imports, generated Convex API paths, or internal English vocabulary without changing product behavior, client-facing Spanish copy, routes, schemas, permissions, public wire contracts, or provider behavior.
---

# Codebase Organizer

Execute exactly one approved vertical checklist item. Treat organization as a source refactor, never as product development or speculative cleanup.

## Required references

Read both references before inspecting or editing the target:

- [canonical-structure.md](references/canonical-structure.md) defines language, folder, filename, and domain-symmetry rules.
- [execution-protocol.md](references/execution-protocol.md) defines the bounded workflow and proof requirements.

Then read repository instructions such as `AGENTS.md`, `CLAUDE.md`, and `package.json`. When Convex is in scope, read `convex/_generated/ai/guidelines.md` completely before editing.

## Non-negotiable boundaries

- Preserve runtime behavior, rendered behavior, data, schema semantics, authorization, function visibility, transaction boundaries, routes, and client-facing Spanish copy.
- Use English for non-client-facing directories, filenames, identifiers, comments, tests, and internal vocabulary.
- Rename a product domain and its correlated backend, frontend, route implementation, adapters, generated references, and tests atomically.
- Preserve hand-authored file contents and Git history. Move every tracked file or directory with `git mv`; never implement a move by deleting and recreating, copying and replacing, or reconstructing the destination file.
- Make content changes as minimal localized patches limited to required import paths, generated API references, and symbol names. Never rewrite a whole hand-authored file when a targeted edit can complete the approved rename, and never run broad formatting over untouched content.
- Preserve precise entity nouns inside a broader product domain. For example, the `sales` domain may contain `ReceiptDetailsDialog` because a receipt remains the entity.
- Do not create barrels, forwarding modules, deprecated aliases, compatibility shims, duplicate exports, or legacy fallback paths.
- Do not change an external REST, MCP, webhook, provider, or other wire contract unless the approved checklist item explicitly authorizes that breaking contract change.
- Do not opportunistically simplify, redesign, optimize, fix unrelated bugs, upgrade dependencies, or rewrite tests.
- Do not edit outside the approved slice except for direct importers, generated references, tests, and adapters required to complete the atomic move.
- If the available tools cannot preserve a tracked file through a move or apply the required change surgically, stop and report the blocker instead of rebuilding the file.
- Never commit, push, comment, approve, merge, or create a pull request when a deterministic controller owns publication.

## Completion contract

Finish only when all old source paths and obsolete internal names covered by the item are gone, imports resolve, focused behavior tests pass, and the controller-owned definitive validation passes. For exac, definitive validation is `pnpm run validate` and must end with zero errors, zero warnings, React Doctor 100, all tests green, and `Convex functions ready`.

If exact behavior cannot be proven or a public consumer is unknown, stop and report the concrete uncertainty. Do not preserve ambiguity with a compatibility layer.

# Execution protocol

## 1. Bind the approved slice

Read `organization.md`. Select only the first top-level unchecked item matching `- [ ] **<id>**`. Its nested bullets define the approved paths, vocabulary, behavior boundary, and contract exclusions.

Record the exact item text and inspect the current Git base before editing. Do not select a later or adjacent item.

## 2. Load project rules

Read repository instruction files and package scripts. If Convex is touched, read the complete generated Convex guidelines. If React or Next.js is touched, load the repository's current React/Next.js guidance when present.

## 3. Build a lightweight dependency map

Use `rg --files`, imports, exports, generated API references, route entrypoints, API/MCP adapters, and tests to identify the full atomic rename surface. Distinguish:

- product-domain vocabulary;
- entity vocabulary;
- client-facing Spanish text;
- repository-controlled generated API consumers;
- external wire contracts that are not authorized to change.

Do not edit until the map proves the slice can be completed without a compatibility layer.

## 4. Refactor atomically

Use `git mv` for every tracked file and directory move so the source inode contents and Git history carry into the destination. Do not use delete-and-create, copy-and-delete, whole-file replacement, or generated-from-memory reconstruction as a substitute for a move.

After moving, apply only targeted localized patches for the exact import paths, generated API references, filenames, and symbol names required by the approved item. Preserve all unrelated lines byte-for-byte where practical. Do not rewrite a hand-authored file, reorder unrelated code, normalize formatting across a file, or replace a component/test wholesale when a surgical edit is sufficient.

Rename symbols and update every direct consumer. Preserve behavior and Spanish client surfaces exactly. Do not add unrelated abstractions or opportunistic cleanup. If a safe move or localized patch is unavailable, stop rather than reconstructing the file.

Generated files are the only whole-file rewrite exception and may be regenerated only by the repository's official command. Do not hand-author generated output.

## 5. Prove absence of legacy structure

Search for every old path, old generated namespace, obsolete English/Spanish internal symbol, and mismatched filename covered by the item. Classify any remaining occurrence as legitimate client copy, entity vocabulary, external contract, or an incomplete rename.

Do not use an alias or forwarding module to make an incomplete rename pass.

## 6. Verify behavior

Run focused tests for affected behavior while iterating. When a deterministic controller owns the final full gate, leave the working tree ready for that controller and do not duplicate its repository-wide validation. Otherwise run the repository's definitive validation yourself.

Review the final diff for behavioral edits, contract drift, schema drift, permission drift, and unexpected generated changes. Confirm tracked moves are represented as renames rather than unrelated deletion/addition pairs. Reject unexplained line churn, broad formatting, or whole-file replacement not produced by an approved generator.

## 7. Update state

Change exactly the selected top-level marker from `[ ]` to `[x]` only after the source refactor and focused checks are complete. Do not rewrite, reorder, or add checklist items during an execution run.

Do not commit or publish when the controller owns Git operations. On uncertainty or failure, leave the selected item unchecked and report the exact blocker.

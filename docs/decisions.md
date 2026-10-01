# Decisions

Assumptions made where the brief/contract was silent. Each picks the option least likely to lose or
corrupt user data or to contradict the contract.

1. **Integration: keep the work on `main`; no push, no pull request.** The delivery rule says the work is
   done when committed on `main` and forbids pushing and opening PRs. The work was committed directly to
   `main`, so there is nothing to merge and nothing to clean up.
2. **`show` reads the files, not the event store.** Files are the source of truth that people edit by
   hand; "importing is not reconciling", so hand edits are not turned into events.
3. **Every write is verified before any file is touched.** The edited texts are re-parsed and the value at
   `PATH` is compared with `VALUE`; on mismatch the command exits 3 and changes nothing.
4. **A mapping/list set onto a node of the same kind is merged key-by-key** (index-by-index for lists),
   so unchanged entries and their comments keep their bytes.
5. **Replacing a value that comes wholly from a YAML include always exits 3**, even if the new value is
   identical; setting keys inside the include writes into the included file.
6. **A `VALUE` that parses as a block collection (e.g. bare `a: b`) is a usage error (exit 2)**: it is
   not a single flow node. Quote it (`'"a: b"'`) to set the string.
7. **Setting a collection to empty writes `{}` / `[]`**, since block style cannot represent an empty
   collection.
8. **A list item replaced by a collection is written compactly (`- k: v`)**; if the item's line carries
   an end-of-line comment, the comment stays on the dash line and the collection goes below it.
9. **A multi-line string that would be written as a literal block, where that block would swallow
   following deeper-indented comment lines, is written double-quoted instead**, so the comments do not
   silently become part of the value.
10. **Event state lives in `.troupe/troupe-state.sqlite`** (the only place the contract allows state).

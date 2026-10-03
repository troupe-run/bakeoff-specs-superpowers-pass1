# Decisions

Assumptions made where the brief/contract was silent. Each picks the option least likely to lose or
corrupt user data or to contradict the contract.

1. **Integration: keep the work on `main`; no push, no pull request.** The delivery rule says the work is
   done when committed on `main` and forbids pushing and opening PRs. The work was committed directly to
   `main`, so there is nothing to merge and nothing to clean up.
2. **`get`/`show` read the files, not the event store.** Files are the source of truth that people edit by
   hand. (Since step 2, `show` reconciles first, so the two agree anyway.)
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
11. **Every load-reconcile-write holds an exclusive `flock` on the `.troupe` directory.** `set` writes its
    files and records its events under the lock, so `watch` can only see the write-back after the events
    exist, and reconciling it emits nothing. Locking the directory needs no extra file in `.troupe/`.
12. **`watch` polls file contents (every 0.1 s) rather than using OS notifications.** It compares the
    current bytes of the config file, every include and the other config name with exactly what it last
    loaded, so a change cannot fall between loading and starting to watch, and includes outside
    `.troupe/` or not yet existing are covered. No new dependency is needed.
13. **If `watch` cannot load the configuration at start, it exits 4** like every other command. If a later
    edit breaks it (e.g. mid-save, or an include whose target is not written yet), it prints the error to
    stderr once and keeps retrying until the files load again.
14. **`history` does not need loadable files once something has been recorded**; it only loads them to
    perform the first import.
15. **Reconcile emits `set`s in document order, then `remove`s**, the same order as `set`'s own events.
16. **`show` writes YAML that reads back as the same configuration.** Keys and strings are quoted only when
    plain style would change their meaning (minimal style: plain, then single, then double quotes); comments
    and the files' own quoting are not carried over. Empty collections are written `{}` / `[]`, and a list
    item that is a collection is written compactly (`- k: v`, `- - x`).
17. **A multi-line string, or a text include, is a literal block only if a literal block can hold it
    exactly**; otherwise (carriage returns, a first line starting with a space, a value of only line
    breaks, ...) it is written double-quoted, escapes and all.
18. **With `--sources`, a literal block's comment goes on its header line** (`key: | # from p.md`): a
    comment on a content line would become part of the value.
19. **`show PATH --sources` on a scalar prints it as YAML (quoted if needed) with the comment**, rather than
    the raw `get` form, since the contract describes `--sources` output as YAML.
20. **Source files outside the theatre directory are named with `..` segments**, still relative to it.

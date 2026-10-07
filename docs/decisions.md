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
   identical; setting existing keys inside the include writes into the included file (new keys: see 24).
   This includes a mapping-form include: replacing the whole include, overrides and all, exits 3.
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
21. **`TROUPE_ACTOR` is checked by every command, not just `set`**; an invalid value exits 2 before
    anything is read or recorded. An empty value is invalid rather than "unset": an agent with a
    misconfigured variable must not be recorded as the logged-in human. The id is everything after the
    first `:` and must be non-empty. If no login name can be found, the default actor's id is `null`.
22. **A command's file reconcile is its own change, separate from the `set` that follows it**: the hand
    edits have actor `unknown`, `via: "file"`, no reason and their own `change` id; `--reason` and the
    CLI actor apply only to the `set`'s own events. `change` ids are random UUIDs; all events of one
    change share one `at`.
23. **Events recorded before lineage existed are upcast, not guessed at.** They get actor `unknown`, `at`
    from the stored event's own timestamp, and `reason: null`. A legacy import keeps `via: "import"` and
    gets `change: "import"` (there is only ever one import, recorded as a unit). A legacy `set`/`remove`
    may have come from the CLI or a hand edit, which cannot be told apart, so its `via` and `change`
    are `null`.
24. **"The file that contains the include reference" is one hop up.** A new key whose deepest existing
    ancestor comes from `c.yml` is written in the file that includes `c.yml`, even if that file is itself
    included. A mapping merged from a mapping-form include takes new keys in its overrides, where they
    already are. A file whose whole content is another include (`file:b.yml`) is converted to
    `$ref: file:b.yml` plus the new keys at the top level.
25. **A scalar-form include in a flow collection is converted to a flow mapping**
    (`{coder: {$ref: file:c.yml, t: 1}}`); one that is a block list item becomes `- $ref: file:c.yml`
    with the overrides aligned below, keeping the end-of-line comment on the dash line. One whose
    `file:` string is not on its key's (or dash's) line is refused (exit 3) rather than reflowed.
26. **A `$ref` whose value is not a `file:` string is an error (exit 4)**, as is a mapping-form text
    include with sibling keys. A mapping-form include with only `$ref` is exactly the include.
27. **If the included YAML is not a mapping, overrides (a mapping) replace it**, following the merge rule
    that anything but two mappings is replaced; with no overrides it is the included value unchanged.
28. **Removing a key from a merged mapping (by setting the mapping) removes it everywhere it is set**:
    from the include and from the overrides, so the merged value is what was asked for.
29. **`--in-include` only decides where new keys go.** A new key goes into the file the deepest existing
    ancestor comes from; under a merged mapping, that is the included file (never the overrides, even if
    they already hold part of the path). Existing values still change where they are set, and without
    any include the flag changes nothing.
30. **Aliases are found where they are written.** PyYAML composes an alias to the anchored node itself, so
    the loader records the position of every `*name` and marks the values reached through one. A write
    that would reach an aliased value anywhere (also by setting an enclosing mapping or list) exits 3,
    even when the value would not change. Replacing an anchored value keeps its anchor and tag.
31. **Merge keys are resolved when reading** (explicit keys win, then earlier merged mappings; merged keys
    sit where the merge key is). A merged value is reached through its alias: setting it exits 3, and so
    does any write that would remove it or touch `<<`. With `--expand-alias` it gets an explicit key
    beside the merge key (`model: sonnet  # expanded from *b by troupe`), which overrides the merged one
    without writing `<<`. A value merged from a mapping written in place (`<<: {..}`) is not expanded.
32. **The expanded copy is the anchored value as read**: nested aliases and merge keys are resolved, and
    an aliased include (`&f file:x.yml`) is copied as its content, so the copy shares nothing with the
    anchor. Comments inside the anchored value are not copied. An alias inside a flow collection cannot
    take a block-style copy: `--expand-alias` exits 3 there. Without an alias on the path,
    `--expand-alias` changes nothing.
33. **A recursive alias (`a: &a {b: *a}`) is a configuration error (exit 4).**
34. **Remote includes are fetched with `urllib`, once per command** (the `set` verification re-reads what
    the command loaded), with a 30 s timeout. HTTP errors, unreachable hosts and non-UTF-8 bodies are
    failed fetches (exit 4), also when the include is new in a `set --include`. `watch` does not poll
    remote includes; it rereads them whenever a local file changes.
35. **A `file:` include inside a remote file is resolved against its URL**, so it is remote too; a remote
    file can never name a local file.
36. **"The include string exactly as written" is its source text** (quotes included, anchor/tag left out),
    with the local file and line: `troupe.yml:2: 'https://…/c.yml'`. For a remote include reached through
    another remote include, that is the local include of the outer one (every such line, if several).
    The remote file's own URL is named too.
37. **Setting a remote value to the value it already has changes nothing and exits 0**, as for any other
    unchanged value; replacing a whole remote include still exits 3 (see 5). `--in-include` and
    `--expand-alias` cannot write into a remote file either (exit 3).
38. **Schema validation uses `jsonschema` (Draft 2020-12)**; `format` is an annotation, not checked. An
    unreadable, non-JSON or invalid schema is a configuration error (exit 4). The configuration is
    validated as loaded: includes resolved, merge keys applied.
39. **The schema decides `format: uri` only through `properties`, `items` and `additionalProperties`**, as
    the contract says: `$ref`, `allOf`, `patternProperties` and the like are not followed. A key matched by
    `patternProperties` does not take `additionalProperties`, and `items` applies after `prefixItems`.
    A value merged from an include or a merge key takes the schema of where it appears.
40. **`!literal` only stops a string being an include**; the value is read as it would be without the tag
    (`!literal 3` is still 3). On a mapping or list it means nothing.
41. **`set` treats every include-like string in VALUE alike**, also inside a flow collection
    (`[file:x, y]` → `- !literal file:x`). A tagged string is written on one line (double-quoted if
    multi-line). Replacing a `!literal` value keeps the tag (one is not added twice); with `--include` the
    tag is removed. `--include` where the schema says `format: uri` exits 3, and with no include-like
    string it changes nothing. With `--include` the write is verified by reading each such string back
    as an include of exactly that string.
42. **`show` tags strings that would be read as includes `!literal`** (unless the schema marks them URIs),
    so its output still reads back as the same configuration.

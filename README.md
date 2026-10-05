# troupe

AI orchestration. This package currently provides the configuration subsystem:

```
uv run troupe config get PATH [--theatre DIR]
uv run troupe config show [PATH] [--sources] [--theatre DIR]
uv run troupe config set PATH VALUE [--in-include] [--reason TEXT] [--theatre DIR]
uv run troupe config history [PATH] [--theatre DIR]
uv run troupe config watch [--theatre DIR]
```

- Configuration lives in `DIR/.troupe/troupe.yml` (or `.yaml`); `file:` strings include other files.
- An include may also be written as a mapping, `{$ref: file:..., key: value}`: the other keys override
  the included YAML (deep-merged: mappings merge, anything else replaces).
- `get` prints one value; `show` prints a value or a whole section as YAML (includes resolved), and with
  `--sources` marks every scalar with the file it comes from.
- `set` writes changes back byte-surgically: only the bytes of the changed value change.
  Existing values change where they are set (an override, else the file they come from); a new key under
  an included section is added as an override in the including file, which never touches the shared
  include. List items and keys inside list items stay in the file that holds the list.
  With `--in-include`, a new key is written into the included file instead.
- The configuration is held as an event-sourced projection (`eventsourcing`, SQLite at
  `.troupe/troupe-state.sqlite`); `history` lists per-leaf `import`/`set`/`remove` events.
- Every command except `history` first reconciles the projection with the files (hand edits become
  `set`/`remove` events); `watch` keeps reconciling as files change, until SIGINT/SIGTERM.
- Every history event carries its lineage: `actor` (`TROUPE_ACTOR=human:<id>|agent:<id>`, default the
  login name; `unknown` for imports and hand edits), `via` (`import`/`cli`/`file`), `at` (UTC), `reason`
  (`set --reason`) and `change`, an id shared by all events of one command, reconcile or import.

Run the tests with `uv run pytest`.

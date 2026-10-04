# troupe

AI orchestration. This package currently provides the configuration subsystem:

```
uv run troupe config get PATH [--theatre DIR]
uv run troupe config show [PATH] [--sources] [--theatre DIR]
uv run troupe config set PATH VALUE [--reason TEXT] [--theatre DIR]
uv run troupe config history [PATH] [--theatre DIR]
uv run troupe config watch [--theatre DIR]
```

- Configuration lives in `DIR/.troupe/troupe.yml` (or `.yaml`); `file:` strings include other files.
- `get` prints one value; `show` prints a value or a whole section as YAML (includes resolved), and with
  `--sources` marks every scalar with the file it comes from.
- `set` writes changes back byte-surgically: only the bytes of the changed value change.
- The configuration is held as an event-sourced projection (`eventsourcing`, SQLite at
  `.troupe/troupe-state.sqlite`); `history` lists per-leaf `import`/`set`/`remove` events.
- Every command except `history` first reconciles the projection with the files (hand edits become
  `set`/`remove` events); `watch` keeps reconciling as files change, until SIGINT/SIGTERM.
- Every history event carries its lineage: `actor` (`TROUPE_ACTOR=human:<id>|agent:<id>`, default the
  login name; `unknown` for imports and hand edits), `via` (`import`/`cli`/`file`), `at` (UTC), `reason`
  (`set --reason`) and `change`, an id shared by all events of one command, reconcile or import.

Run the tests with `uv run pytest`.

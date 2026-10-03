# troupe

AI orchestration. This package currently provides the configuration subsystem:

```
uv run troupe config show PATH [--theatre DIR]
uv run troupe config set PATH VALUE [--theatre DIR]
uv run troupe config history [PATH] [--theatre DIR]
uv run troupe config watch [--theatre DIR]
```

- Configuration lives in `DIR/.troupe/troupe.yml` (or `.yaml`); `file:` strings include other files.
- `set` writes changes back byte-surgically: only the bytes of the changed value change.
- The configuration is held as an event-sourced projection (`eventsourcing`, SQLite at
  `.troupe/troupe-state.sqlite`); `history` lists per-leaf `import`/`set`/`remove` events.
- Every command except `history` first reconciles the projection with the files (hand edits become
  `set`/`remove` events); `watch` keeps reconciling as files change, until SIGINT/SIGTERM.

Run the tests with `uv run pytest`.

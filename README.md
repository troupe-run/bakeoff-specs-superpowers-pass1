# troupe

AI orchestration. This package currently provides the configuration subsystem:

```
uv run troupe config get PATH [--theatre DIR]
uv run troupe config show [PATH] [--sources] [--theatre DIR]
uv run troupe config set PATH VALUE [--include] [--in-include] [--expand-alias] [--reason TEXT] [--theatre DIR]
uv run troupe config history [PATH] [--theatre DIR]
uv run troupe config watch [--theatre DIR]
```

- Configuration lives in `DIR/.troupe/troupe.yml` (or `.yaml`); `file:` strings include other files.
- `https://` and `http://` strings are remote includes, fetched on every load (YAML if the URL path ends
  `.yml`/`.yaml`, else text; a failed fetch exits 4). They are read-only: changing a value that comes from
  one exits 3 and names the include line in the local file to change instead. New keys under one become
  local overrides. A `file:` include inside a remote file is relative to its URL.
- `.troupe/troupe.schema.json` (JSON Schema 2020-12), if present, must validate the loaded configuration
  (else exit 4; a `set` that would break it exits 3). A string whose schema (through `properties`, `items`,
  `additionalProperties`) has `"format": "uri"`, or that is tagged `!literal`, is a literal, never an
  include. `set` writes a string that would read as an include, at a path the schema does not mark as a
  URI, tagged `!literal`, unless `--include` is given; `show` tags such strings the same way.
- An include may also be written as a mapping, `{$ref: file:..., key: value}`: the other keys override
  the included YAML (deep-merged: mappings merge, anything else replaces).
- `get` prints one value; `show` prints a value or a whole section as YAML (includes resolved), and with
  `--sources` marks every scalar with the file it comes from.
- `set` writes changes back byte-surgically: only the bytes of the changed value change.
  Existing values change where they are set (an override, else the file they come from); a new key under
  an included section is added as an override in the including file, which never touches the shared
  include. List items and keys inside list items stay in the file that holds the list.
  With `--in-include`, a new key is written into the included file instead.
- Aliases: setting a value defined at an anchor (`&name`) changes it there, and so everywhere `*name` is
  used. Setting a path reached through an alias, or through a merge key (`<<: *name`), exits 3; with
  `--expand-alias`, that alias is replaced by a block-style copy of the anchored value (its key's line
  ends `  # expanded from *name by troupe`) and the value is set in the copy. Merge keys are resolved
  when reading and never written.
- The configuration is held as an event-sourced projection (`eventsourcing`, SQLite at
  `.troupe/troupe-state.sqlite`); `history` lists per-leaf `import`/`set`/`remove` events.
- Every command except `history` first reconciles the projection with the files (hand edits become
  `set`/`remove` events); `watch` keeps reconciling as files change, until SIGINT/SIGTERM.
- Every history event carries its lineage: `actor` (`TROUPE_ACTOR=human:<id>|agent:<id>`, default the
  login name; `unknown` for imports and hand edits), `via` (`import`/`cli`/`file`), `at` (UTC), `reason`
  (`set --reason`) and `change`, an id shared by all events of one command, reconcile or import.

Run the tests with `uv run pytest`.

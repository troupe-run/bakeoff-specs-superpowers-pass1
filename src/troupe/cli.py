"""`troupe config show|set|history`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, PathNotFound, WriteRefused, flatten, load, lookup, split_path
from .store import ConfigStore
from .writer import Writer
from .yamlcore import ValueError_, format_scalar, parse_value, same


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # exit 2 with an empty stdout
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="troupe")
    sub = parser.add_subparsers(dest="group", required=True)
    config = sub.add_parser("config", help="show and change the theatre configuration")
    cmds = config.add_subparsers(dest="cmd", required=True)
    for name, extra in (("show", ["path"]), ("set", ["path", "value"]), ("history", ["path?"])):
        p = cmds.add_parser(name)
        p.add_argument("--theatre", default=".", help="theatre directory (default: current directory)")
        for arg in extra:
            if arg.endswith("?"):
                p.add_argument(arg[:-1], nargs="?")
            else:
                p.add_argument(arg)
    return parser


def _out(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        print(f"troupe: usage error: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"troupe: {exc}", file=sys.stderr)
        return 4
    except WriteRefused as exc:
        print(f"troupe: write refused: {exc}", file=sys.stderr)
        return 3
    except PathNotFound as exc:
        print(f"troupe: {exc}", file=sys.stderr)
        return 5


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    segs: list[str] | None = None
    if args.path is not None:
        try:
            segs = split_path(args.path)
        except ValueError as exc:
            raise UsageError(str(exc)) from exc
    value = None
    if args.cmd == "set":
        try:
            value = parse_value(args.value)
        except ValueError_ as exc:
            raise UsageError(f"invalid VALUE {args.value!r}: {exc}") from exc

    theatre = Path(args.theatre)
    cfg = load(theatre)
    store = ConfigStore(cfg.config_path.parent)
    store.ensure_imported(flatten(cfg.root))

    if args.cmd == "show":
        item = lookup(cfg.root, segs, args.path)
        if item.kind not in ("scalar", "text"):
            raise PathNotFound(f"{args.path}: is a {'mapping' if item.kind == 'map' else 'list'}, not a scalar")
        text = format_scalar(item.value)
        _out(text if text.endswith("\n") else text + "\n")
        return 0

    if args.cmd == "set":
        return _set(theatre, cfg, store, segs, args.path, value)

    for entry in store.history():
        p = entry["path"]
        if args.path is None or p == args.path or p.startswith(args.path + "."):
            _out(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


def _set(theatre: Path, cfg, store: ConfigStore, segs: list[str], path: str, value) -> int:
    writer = Writer()
    writer.set_path(cfg.root, segs, value, path)
    results = {p: t for p, t in writer.results().items() if p not in writer.docs or t != writer.docs[p].text}
    if not results:
        return 0
    # Check the planned write before touching any file.
    try:
        new_cfg = load(theatre, overrides=results)
        written = lookup(new_cfg.root, segs, path)
    except (ConfigError, PathNotFound) as exc:
        raise WriteRefused(f"{path}: cannot write this change faithfully ({exc})") from exc
    expected = writer.raw.get(written.include, value.value) if written.kind == "text" else value.py()
    if not same(written.py(), expected):
        raise WriteRefused(f"{path}: cannot write this change faithfully")
    for p, text in results.items():
        p.write_bytes(text.encode("utf-8"))

    before, after = flatten(cfg.root), flatten(new_cfg.root)
    changes: list[tuple[str, str, Any, Any]] = []
    for p, v in after.items():
        if p not in before:
            changes.append(("set", p, None, v))
        elif not same(before[p], v):
            changes.append(("set", p, before[p], v))
    for p, v in before.items():
        if p not in after:
            changes.append(("remove", p, v, None))
    store.record(changes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

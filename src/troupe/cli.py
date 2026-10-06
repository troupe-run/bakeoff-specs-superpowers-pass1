"""`troupe config get|show|set|history|watch`."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import signal
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from .config import (
    Config,
    ConfigError,
    Item,
    PathNotFound,
    WriteRefused,
    find_config,
    flatten,
    load,
    lookup,
    split_path,
)
from .dump import dump, source_namer
from .store import STATE_FILE, ConfigStore, diff, lineage
from .writer import Writer, expand, node_val, shared_on_path
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
    commands = (
        ("get", ["path"]),
        ("show", ["path?"]),
        ("set", ["path", "value"]),
        ("history", ["path?"]),
        ("watch", []),
    )
    for name, extra in commands:
        p = cmds.add_parser(name)
        p.add_argument(
            "--theatre", default=None, help="theatre directory (default: $TROUPE_THEATRE, else current directory)"
        )
        for arg in extra:
            if arg.endswith("?"):
                p.add_argument(arg[:-1], nargs="?")
            else:
                p.add_argument(arg)
        if name == "show":
            p.add_argument("--sources", action="store_true", help="say which file each value comes from")
        if name == "set":
            p.add_argument("--reason", default=None, help="why the change is made (recorded in history)")
            p.add_argument(
                "--in-include",
                action="store_true",
                help="write a new key into the included file instead of as an override in the including file",
            )
            p.add_argument(
                "--expand-alias",
                action="store_true",
                help="replace the alias on PATH with its own copy of the anchored value, then set the copy",
            )
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


def actor_from_env() -> dict[str, str | None]:
    """The actor behind a CLI command: `TROUPE_ACTOR` (`human:<id>` / `agent:<id>`), else the login name."""
    spec = os.environ.get("TROUPE_ACTOR")
    if spec is None:
        try:
            login: str | None = getpass.getuser() or None
        except (OSError, KeyError):
            login = None  # no login name to be found: say so rather than guess
        return {"kind": "human", "id": login}
    kind, sep, ident = spec.partition(":")
    if not sep or kind not in ("human", "agent") or not ident:
        raise UsageError(f"TROUPE_ACTOR must be human:<id> or agent:<id>, not {spec!r}")
    return {"kind": kind, "id": ident}


def run(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    actor = actor_from_env()
    segs: list[str] | None = None
    if getattr(args, "path", None) is not None:
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

    shown = args.theatre if args.theatre is not None else os.environ.get("TROUPE_THEATRE") or "."
    theatre = Path(shown)
    troupe_dir = find_config(theatre).parent

    if args.cmd == "watch":
        return _watch(theatre, troupe_dir, shown)

    with _locked(troupe_dir):
        if args.cmd == "history":
            # History reports what has been recorded; it never reconciles.
            if (troupe_dir / STATE_FILE).exists():
                entries = ConfigStore(troupe_dir).history()
            else:
                leaves = flatten(load(theatre).root)
                store = ConfigStore(troupe_dir)
                store.ensure_imported(leaves)
                entries = store.history()
        else:
            cfg = load(theatre)
            store = ConfigStore(troupe_dir)
            _reconcile(store, cfg)
            if args.cmd == "set":
                how = lineage("cli", actor, args.reason)
                return _set(theatre, cfg, store, segs, args.path, value, how, args.in_include, args.expand_alias)

    if args.cmd in ("get", "show"):
        item = lookup(cfg.root, segs, args.path) if segs is not None else cfg.root
        if args.cmd == "show" and (args.sources or item.kind in ("map", "seq")):
            _out(dump(item, source_namer(theatre) if args.sources else None))
            return 0
        if item.kind in ("map", "seq"):
            raise PathNotFound(
                f"{args.path}: is a {'mapping' if item.kind == 'map' else 'list'}, not a scalar;"
                f" use `troupe config show {args.path}` to print it"
            )
        text = format_scalar(item.value)
        _out(text if text.endswith("\n") else text + "\n")
        return 0

    for entry in entries:
        p = entry["path"]
        if args.path is None or p == args.path or p.startswith(args.path + "."):
            _out(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


@contextmanager
def _locked(troupe_dir: Path):
    """Hold an exclusive lock on the `.troupe` directory.

    Every load-reconcile-write runs under it, so `watch` never sees a write-back by `set` before `set`
    has recorded its events.
    """
    fd = os.open(troupe_dir, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _reconcile(store: ConfigStore, cfg: Config) -> None:
    """Bring the recorded configuration in line with the loaded files. Call with the lock held."""
    leaves = flatten(cfg.root)
    if store.exists():
        store.reconcile(leaves)
    else:
        store.ensure_imported(leaves)


def _set(
    theatre: Path,
    cfg,
    store: ConfigStore,
    segs: list[str],
    path: str,
    value,
    how: dict,
    in_include: bool = False,
    expand_alias: bool = False,
) -> int:
    planned: dict[Path, str] = {}
    target = cfg
    shared, depth = shared_on_path(cfg.root, segs) if expand_alias else (None, 0)
    if shared is not None:
        # Give the aliased use its own copy, then set the value in the copy.
        expander = Writer()
        expand(expander, shared, value if depth == len(segs) else node_val(shared.node))
        planned = expander.results()
        if depth < len(segs):
            try:
                target = load(theatre, overrides=planned)
            except ConfigError as exc:
                raise WriteRefused(f"{path}: cannot expand the alias faithfully ({exc})") from exc
    if shared is None or depth < len(segs):
        writer = Writer(in_include)
        writer.set_path(target.root, segs, value, path)
        planned.update(writer.results())
    results = {p: t for p, t in planned.items() if p not in cfg.docs or t != cfg.docs[p].text}
    if not results:
        return 0
    # Check the planned write before touching any file.
    try:
        new_cfg = load(theatre, overrides=results)
        written = lookup(new_cfg.root, segs, path)
    except (ConfigError, PathNotFound) as exc:
        raise WriteRefused(f"{path}: cannot write this change faithfully ({exc})") from exc
    expected = results.get(written.include, value.value) if written.kind == "text" else value.py()
    if not same(written.py(), expected):
        raise WriteRefused(f"{path}: cannot write this change faithfully")
    for p, text in results.items():
        p.write_bytes(text.encode("utf-8"))

    store.record(diff(flatten(cfg.root), flatten(new_cfg.root)), how)
    return 0


# ---------------------------------------------------------------------------- watch

POLL_SECONDS = 0.1


def _loaded_texts(cfg: Config) -> dict[Path, str | None]:
    """The contents `load` read, by file: the config file, its includes, and the absent other config name."""
    texts: dict[Path, str | None] = {p: doc.text for p, doc in cfg.docs.items()}
    for name in ("troupe.yml", "troupe.yaml"):
        texts.setdefault(cfg.config_path.parent / name, None)

    def walk(item: Item) -> None:
        if item.kind == "text":
            texts[item.include] = item.value
        elif item.kind == "map":
            for child in item.value.values():
                walk(child)
        elif item.kind == "seq":
            for child in item.value:
                walk(child)

    walk(cfg.root)
    return texts


def _unchanged(texts: dict[Path, str | None]) -> bool:
    for p, text in texts.items():
        try:
            current: str | None = p.read_bytes().decode("utf-8")
        except FileNotFoundError:
            current = None
        except (OSError, UnicodeDecodeError):
            return False
        if current != text:
            return False
    return True


def _watch(theatre: Path, troupe_dir: Path, shown: str) -> int:
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with _locked(troupe_dir):
            cfg = load(theatre)
            store = ConfigStore(troupe_dir)
            _reconcile(store, cfg)
        texts: dict[Path, str | None] | None = _loaded_texts(cfg)
        # Every later change makes the files differ from `texts`, which is what was reconciled.
        _out(f"watching {shown}\n")
        error = None
        while not stop.wait(POLL_SECONDS):
            if texts is not None and _unchanged(texts):
                continue
            try:
                with _locked(troupe_dir):
                    cfg = load(theatre)
                    _reconcile(store, cfg)
                texts = _loaded_texts(cfg)
                error = None
            except ConfigError as exc:
                # Mid-edit or broken: keep retrying until the files load again.
                texts = None
                if str(exc) != error:
                    error = str(exc)
                    print(f"troupe: {exc}", file=sys.stderr, flush=True)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())

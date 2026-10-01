"""Loading a theatre's configuration, following includes, into a tree of located items."""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .yamlcore import scalar_value


class ConfigError(Exception):
    """Exit 4: the configuration cannot be loaded."""


class PathNotFound(Exception):
    """Exit 5: path not found, or the wrong kind of node."""


class WriteRefused(Exception):
    """Exit 3: the write is refused."""


_BREAK = re.compile(r"\r\n|\r|\n")


class Doc:
    """A YAML file: its text, composed node tree, line index, and pending edits."""

    def __init__(self, path: Path, text: str):
        self.path = path
        self.text = text
        try:
            self.root: yaml.Node | None = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: invalid YAML: {str(exc).splitlines()[0]}") from exc
        self.line_starts = [0] + [m.end() for m in _BREAK.finditer(text)]
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.indent = _detect_indent(self.root)
        self.edits: list[tuple[int, int, int, str]] = []

    # line helpers -----------------------------------------------------------
    def line_of(self, idx: int) -> int:
        return bisect.bisect_right(self.line_starts, idx) - 1

    def line_start(self, ln: int) -> int:
        return self.line_starts[ln]

    def line_end(self, ln: int) -> int:
        """Index of the end of line `ln`, excluding its line break."""
        if ln + 1 < len(self.line_starts):
            end = self.line_starts[ln + 1]
            while end > self.line_starts[ln] and self.text[end - 1] in "\r\n":
                end -= 1
            return end
        return len(self.text)

    def has_break(self, ln: int) -> bool:
        return ln + 1 < len(self.line_starts)

    def line_count(self) -> int:
        n = len(self.line_starts)
        if self.line_starts[-1] == len(self.text):
            n -= 1  # text ends with a line break: no partial last line
        return n

    def line_text(self, ln: int) -> str:
        return self.text[self.line_start(ln) : self.line_end(ln)]

    def col(self, idx: int) -> int:
        return idx - self.line_start(self.line_of(idx))

    # edits ------------------------------------------------------------------
    def edit(self, start: int, end: int, new: str) -> None:
        if self.text[start:end] == new:
            return
        self.edits.append((start, end, len(self.edits), new))

    def edited_text(self) -> str:
        text = self.text
        last_start = len(text) + 1
        for start, end, _, new in sorted(self.edits, key=lambda e: (e[0], e[1], e[2]), reverse=True):
            if end > last_start:
                raise WriteRefused(f"{self.path}: overlapping edits")
            text = text[:start] + new + text[end:]
            last_start = start
        return text


def _detect_indent(root: yaml.Node | None) -> int:
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                if (
                    isinstance(v, (yaml.MappingNode, yaml.SequenceNode))
                    and not v.flow_style
                    and v.value
                    and v.start_mark.line > k.start_mark.line
                    and v.start_mark.column > k.start_mark.column
                ):
                    return v.start_mark.column - k.start_mark.column
                stack.append(v)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return 2


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: include target not found") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc


@dataclass(eq=False)
class Item:
    """A configuration value located in a file.

    kind: map | seq | scalar | text (a text include).
    node: the YAML node in `doc` (for `text`, the `file:` string node).
    include: the include target when this value came from an include.
    parent/key: position in the containing collection (None for a document root).
    """

    kind: str
    doc: Doc
    node: yaml.Node | None
    value: Any = None
    include: Path | None = None
    parent: "Item | None" = None
    key: Any = None  # str for mapping entries, int for sequence items
    key_node: yaml.Node | None = None
    flow: bool = False  # inside a flow collection

    @property
    def yaml_include(self) -> bool:
        return self.include is not None and self.kind != "text"

    def py(self) -> Any:
        if self.kind == "map":
            return {k: v.py() for k, v in self.value.items()}
        if self.kind == "seq":
            return [v.py() for v in self.value]
        return self.value


@dataclass
class Config:
    config_path: Path
    root: Item
    docs: dict[Path, Doc] = field(default_factory=dict)


def find_config(theatre: Path) -> Path:
    base = theatre / ".troupe"
    found = [p for p in (base / "troupe.yml", base / "troupe.yaml") if p.is_file()]
    if not found:
        raise ConfigError(f"{base}/troupe.yml: no configuration file found")
    if len(found) > 1:
        raise ConfigError(f"{base}: both troupe.yml and troupe.yaml exist")
    return found[0]


def load(theatre: Path, overrides: dict[Path, str] | None = None) -> Config:
    """Load the configuration; `overrides` substitutes file contents (to check a planned write)."""
    path = find_config(theatre).resolve()
    docs: dict[Path, Doc] = {}
    overrides = overrides or {}

    def text_of(p: Path) -> str:
        return overrides[p] if p in overrides else read_text(p)

    def get_doc(p: Path) -> Doc:
        if p not in docs:
            docs[p] = Doc(p, text_of(p))
        return docs[p]

    def build(doc: Doc, node: yaml.Node | None, stack: tuple[Path, ...], flow: bool) -> Item:
        if node is None:
            return Item("scalar", doc, None, None)
        if isinstance(node, yaml.MappingNode):
            item = Item("map", doc, node, {}, flow=flow)
            for k, v in node.value:
                if not isinstance(k, yaml.ScalarNode):
                    raise ConfigError(f"{doc.path}: complex mapping keys are not supported")
                child = build(doc, v, stack, bool(node.flow_style))
                child.parent, child.key, child.key_node = item, k.value, k
                item.value[k.value] = child
            return item
        if isinstance(node, yaml.SequenceNode):
            item = Item("seq", doc, node, [], flow=flow)
            for i, v in enumerate(node.value):
                child = build(doc, v, stack, bool(node.flow_style))
                child.parent, child.key = item, i
                item.value.append(child)
            return item
        value = scalar_value(node)
        if isinstance(value, str) and value.startswith("file:"):
            target = (doc.path.parent / value[5:]).resolve()
            if target in stack:
                raise ConfigError(f"{doc.path}: include cycle through {target}")
            if not target.is_file():
                raise ConfigError(f"{doc.path}: include target {target} not found")
            if target.suffix in (".yml", ".yaml"):
                tdoc = get_doc(target)
                item = build(tdoc, tdoc.root, stack + (target,), False)
                item.include = target
                item.flow = flow
                return item
            return Item("text", doc, node, text_of(target), include=target, flow=flow)
        return Item("scalar", doc, node, value, flow=flow)

    root_doc = get_doc(path)
    root = build(root_doc, root_doc.root, (path,), False)
    return Config(path, root, docs)


def flatten(item: Item, prefix: str = "") -> dict[str, Any]:
    """Leaves of the configuration, by dotted path, in document order."""
    out: dict[str, Any] = {}
    if item.kind == "map":
        for k, v in item.value.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif item.kind == "seq":
        for i, v in enumerate(item.value):
            out.update(flatten(v, f"{prefix}{i}."))
    elif prefix:
        out[prefix[:-1]] = item.value
    return out


def split_path(path: str) -> list[str]:
    segs = path.split(".")
    if not path or any(s == "" for s in segs):
        raise ValueError(f"invalid path: {path!r}")
    return segs


def lookup(root: Item, segs: list[str], path: str) -> Item:
    item = root
    for seg in segs:
        if item.kind == "map" and seg in item.value:
            item = item.value[seg]
        elif item.kind == "seq" and seg.isdigit() and int(seg) < len(item.value):
            item = item.value[int(seg)]
        else:
            raise PathNotFound(f"path not found: {path}")
    return item

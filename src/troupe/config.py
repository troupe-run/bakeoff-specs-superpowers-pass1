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

MERGE = "<<"


@dataclass(frozen=True)
class Alias:
    """An alias (`*name`) in a file: the anchor's name and the span of the `*name` token."""

    name: str
    start: int
    end: int


MERGE_INLINE = Alias(MERGE, -1, -1)  # `merged` of a value merged from a mapping written in place


def is_merge(key: yaml.Node) -> bool:
    return isinstance(key, yaml.ScalarNode) and key.value == MERGE and key.style is None


class _Loader(yaml.SafeLoader):
    """Composes like SafeLoader, and records where each alias is used.

    An alias composes to the anchored node itself, so the node cannot tell its uses apart; `aliases` maps
    (id of the collection, id of the key node or the item index) to the alias used there.
    """

    def __init__(self, stream: str):
        super().__init__(stream)
        self.aliases: dict[tuple[int, Any], Alias] = {}

    def compose_node(self, parent, index):
        if index is not None and self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            node = super().compose_node(parent, index)
            at = id(index) if isinstance(index, yaml.Node) else index
            self.aliases[(id(parent), at)] = Alias(event.anchor, event.start_mark.index, event.end_mark.index)
            return node
        return super().compose_node(parent, index)


class Doc:
    """A YAML file: its text, composed node tree, line index, and pending edits."""

    def __init__(self, path: Path, text: str):
        self.path = path
        self.text = text
        loader = _Loader(text)
        try:
            self.root: yaml.Node | None = loader.get_single_node()
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: invalid YAML: {str(exc).splitlines()[0]}") from exc
        finally:
            loader.dispose()
        self.aliases = loader.aliases
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
    def alias_at(self, parent: yaml.Node, at: yaml.Node | int) -> Alias | None:
        """The alias used as the value of key node (or item index) `at` of collection `parent`, if any."""
        return self.aliases.get((id(parent), id(at) if isinstance(at, yaml.Node) else at))

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
    stack, seen = [root], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:  # aliases share nodes, and may even be recursive
            continue
        seen.add(id(node))
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
    ref: for the root of a YAML include, the including file and the `file:` string (scalar form) or
        `$ref` mapping (mapping form) that includes it.
    parts: for a mapping merged from an include and its overrides, the merged mappings (included part
        first); `doc`/`node` are then the overrides' mapping, where new keys go.
    alias: the alias this value is used through, when it is written `*name` here.
    merged: the alias of the mapping a merge key (`<<`) of the parent mapping takes this value from
        (`MERGE_INLINE` if that mapping is written in place).
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
    ref: "tuple[Doc, yaml.Node] | None" = None
    parts: "list[Item] | None" = None
    alias: Alias | None = None
    merged: Alias | None = None

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

    def build(doc: Doc, node: yaml.Node | None, stack: tuple[Path, ...], flow: bool, nodes: tuple = ()) -> Item:
        if node is None:
            return Item("scalar", doc, None, None)
        if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)):
            if any(n is node for n in nodes):
                raise ConfigError(f"{doc.path}: recursive alias")
            nodes = nodes + (node,)
        if isinstance(node, yaml.MappingNode):
            ref = None
            item = Item("map", doc, node, {}, flow=flow)
            explicit = {k.value for k, _ in node.value if isinstance(k, yaml.ScalarNode) and not is_merge(k)}
            for k, v in node.value:
                if not isinstance(k, yaml.ScalarNode):
                    raise ConfigError(f"{doc.path}: complex mapping keys are not supported")
                if k.value == "$ref":
                    ref = v
                    continue
                if is_merge(k):
                    # Merged keys sit where the merge key is; explicit keys win, then earlier mappings.
                    for source, alias in merge_sources(doc, node, k, v, stack, nodes):
                        for key, child in source.value.items():
                            if key not in explicit and key not in item.value:
                                child.parent, child.merged = item, alias or MERGE_INLINE
                                item.value[key] = child
                    continue
                child = build(doc, v, stack, bool(node.flow_style), nodes)
                child.parent, child.key, child.key_node = item, k.value, k
                child.alias = doc.alias_at(node, k)
                item.value[k.value] = child
            if ref is None:
                return item
            # Mapping form: the included content, deep-merged with the other keys as overrides.
            value = scalar_value(ref) if isinstance(ref, yaml.ScalarNode) else None
            if not (isinstance(value, str) and value.startswith("file:")):
                raise ConfigError(f"{doc.path}: $ref must be a file: include")
            included = include(doc, ref, value, stack, flow)
            if included.kind == "text":
                if item.value:
                    raise ConfigError(f"{doc.path}: overrides apply only to YAML includes, not {included.include}")
                return included
            if included.kind != "map" and not item.value:
                return included
            merged = merge(included, item)
            merged.include = included.include
            return merged
        if isinstance(node, yaml.SequenceNode):
            item = Item("seq", doc, node, [], flow=flow)
            for i, v in enumerate(node.value):
                child = build(doc, v, stack, bool(node.flow_style), nodes)
                child.parent, child.key = item, i
                child.alias = doc.alias_at(node, i)
                item.value.append(child)
            return item
        value = scalar_value(node)
        if isinstance(value, str) and value.startswith("file:"):
            item = include(doc, node, value, stack, flow)
            if item.kind != "text" and item.ref is None:  # a whole-file include keeps the innermost
                item.ref = (doc, node)
            return item
        return Item("scalar", doc, node, value, flow=flow)

    def merge_sources(
        doc: Doc, node: yaml.Node, k: yaml.Node, v: yaml.Node, stack: tuple[Path, ...], nodes: tuple
    ) -> list[tuple[Item, Alias | None]]:
        """The mappings merged by merge key `k` (value `v`): one mapping, or a list of them."""
        if isinstance(v, yaml.SequenceNode):
            sources = [(x, doc.alias_at(v, i)) for i, x in enumerate(v.value)]
        else:
            sources = [(v, doc.alias_at(node, k))]
        out = []
        for source, alias in sources:
            if not isinstance(source, yaml.MappingNode):
                raise ConfigError(f"{doc.path}: a merge key (<<) must merge mappings")
            out.append((build(doc, source, stack, bool(node.flow_style), nodes), alias))
        return out

    def include(doc: Doc, node: yaml.Node, value: str, stack: tuple[Path, ...], flow: bool) -> Item:
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

    root_doc = get_doc(path)
    root = build(root_doc, root_doc.root, (path,), False)
    return Config(path, root, docs)


def merge(base: Item, over: Item) -> Item:
    """`base` deep-merged with `over`: mappings merge recursively, anything else in `over` replaces.

    The result takes `over`'s position (parent, key).
    """
    if base.kind != "map" or over.kind != "map":
        return over
    item = Item("map", over.doc, over.node, {}, flow=over.flow, parts=[base, over])
    item.parent, item.key, item.key_node = over.parent, over.key, over.key_node
    item.alias, item.merged = over.alias, over.merged
    for k, child in base.value.items():
        if k in over.value:
            child = merge(child, over.value[k])
        child.parent = item
        item.value[k] = child
    for k, child in over.value.items():
        if k not in base.value:
            child.parent = item
            item.value[k] = child
    return item


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

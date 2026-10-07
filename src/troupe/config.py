"""Loading a theatre's configuration, following includes, into a tree of located items."""

from __future__ import annotations

import bisect
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import schema as schemas
from .yamlcore import scalar_value


class ConfigError(Exception):
    """Exit 4: the configuration cannot be loaded."""


class FetchError(ConfigError):
    """Exit 4: a remote include cannot be fetched."""


class PathNotFound(Exception):
    """Exit 5: path not found, or the wrong kind of node."""


class WriteRefused(Exception):
    """Exit 3: the write is refused."""


_BREAK = re.compile(r"\r\n|\r|\n")

MERGE = "<<"

LITERAL = "!literal"  # the tag that makes an include-like string a literal
REMOTE = ("https://", "http://")
FETCH_TIMEOUT = 30


def include_like(value: Any) -> bool:
    """Whether a string would be read as an include (unless the schema or a `!literal` tag says otherwise)."""
    return isinstance(value, str) and value.startswith(("file:", *REMOTE))


def is_remote(location: Path | str | None) -> bool:
    """Whether an include target (or a `Doc.path`) is a URL rather than a local file."""
    return isinstance(location, str)


_PROPS = re.compile(r"(?:[&!][^ \t\r\n]*[ \t]+)*")


@dataclass(frozen=True)
class Origin:
    """An include string as written in a local file, and its line (1-based)."""

    path: Path
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text}"


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

    def __init__(self, path: Path | str, text: str):
        self.path = path  # a URL for a remote include
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
        self.origins: list[Origin] = []  # remote: the local include strings that lead to it

    @property
    def remote(self) -> bool:
        return is_remote(self.path)

    def origins_of(self, node: yaml.Node) -> list[Origin]:
        """Where the include string `node` (in this file) is written locally."""
        if self.remote:
            return self.origins
        start, end = node.start_mark.index, node.end_mark.index
        text = self.text[start:end]
        text = text[_PROPS.match(text).end() :]
        return [Origin(self.path, self.line_of(start) + 1, text)]

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


def fetch(url: str, cache: dict[str, str]) -> str:
    """The text at `url`, fetched once per `cache`."""
    if url not in cache:
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response:
                data = response.read()
            cache[url] = data.decode("utf-8")
        except (OSError, ValueError, http.client.HTTPException) as exc:  # URLError is an OSError
            raise FetchError(f"{url}: cannot fetch remote include: {exc}") from exc
    return cache[url]


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
    include: the include target when this value came from an include (a URL for a remote include).
    parent/key: position in the containing collection (None for a document root).
    ref: for the root of a YAML include, the including file and the `file:` string (scalar form) or
        `$ref` mapping (mapping form) that includes it.
    parts: for a mapping merged from an include and its overrides, the merged mappings (included part
        first); `doc`/`node` are then the overrides' mapping, where new keys go.
    alias: the alias this value is used through, when it is written `*name` here.
    merged: the alias of the mapping a merge key (`<<`) of the parent mapping takes this value from
        (`MERGE_INLINE` if that mapping is written in place).
    via: the include string this value is read from, when it is written as a scalar-form include.
    uri: the schema marks this value `format: uri`.
    """

    kind: str
    doc: Doc
    node: yaml.Node | None
    value: Any = None
    include: Path | str | None = None
    parent: "Item | None" = None
    key: Any = None  # str for mapping entries, int for sequence items
    key_node: yaml.Node | None = None
    flow: bool = False  # inside a flow collection
    ref: "tuple[Doc, yaml.Node] | None" = None
    parts: "list[Item] | None" = None
    alias: Alias | None = None
    merged: Alias | None = None
    via: str | None = None
    uri: bool = False

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
    docs: dict[Path | str, Doc] = field(default_factory=dict)
    schema: Any = None
    fetched: dict[str, str] = field(default_factory=dict)
    schema_text: str | None = None  # the text the schema was read from (None: no schema file)


def find_config(theatre: Path) -> Path:
    base = theatre / ".troupe"
    found = [p for p in (base / "troupe.yml", base / "troupe.yaml") if p.is_file()]
    if not found:
        raise ConfigError(f"{base}/troupe.yml: no configuration file found")
    if len(found) > 1:
        raise ConfigError(f"{base}: both troupe.yml and troupe.yaml exist")
    return found[0]


def load(theatre: Path, overrides: dict[Path, str] | None = None, fetched: dict[str, str] | None = None) -> Config:
    """Load the configuration.

    `overrides` substitutes file contents (to check a planned write); `fetched` caches remote includes
    (pass the one of an earlier load to read them as that load did).
    """
    path = find_config(theatre).resolve()
    try:
        schema, schema_text = schemas.load_schema(path.parent)
    except schemas.SchemaProblem as exc:
        raise ConfigError(str(exc)) from exc
    docs: dict[Path | str, Doc] = {}
    overrides = overrides or {}
    fetched = {} if fetched is None else fetched

    def text_of(p: Path | str) -> str:
        if is_remote(p):
            return fetch(p, fetched)
        return overrides[p] if p in overrides else read_text(p)

    def get_doc(p: Path | str) -> Doc:
        if p not in docs:
            docs[p] = Doc(p, text_of(p))
        return docs[p]

    def build(
        doc: Doc, node: yaml.Node | None, stack: tuple, flow: bool, nodes: tuple = (), schema: Any = None
    ) -> Item:
        """The value of `node`; `schema` is the schema of its position in the configuration."""
        if node is None:
            return Item("scalar", doc, None, None, uri=schemas.is_uri(schema))
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
                    for source, alias in merge_sources(doc, node, k, v, stack, nodes, schema):
                        for key, child in source.value.items():
                            if key not in explicit and key not in item.value:
                                child.parent, child.merged = item, alias or MERGE_INLINE
                                item.value[key] = child
                    continue
                child = build(doc, v, stack, bool(node.flow_style), nodes, schemas.prop_schema(schema, k.value))
                child.parent, child.key, child.key_node = item, k.value, k
                child.alias = doc.alias_at(node, k)
                item.value[k.value] = child
            if ref is None:
                return item
            # Mapping form: the included content, deep-merged with the other keys as overrides.
            value = scalar_value(ref) if isinstance(ref, yaml.ScalarNode) else None
            if not include_like(value):
                raise ConfigError(f"{doc.path}: $ref must be an include (file: or a URL)")
            included = include(doc, ref, value, stack, flow, schema)
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
                child = build(doc, v, stack, bool(node.flow_style), nodes, schemas.item_schema(schema, i))
                child.parent, child.key = item, i
                child.alias = doc.alias_at(node, i)
                item.value.append(child)
            return item
        value = scalar_value(node)
        uri = schemas.is_uri(schema)
        if include_like(value) and not uri and node.tag != LITERAL:
            item = include(doc, node, value, stack, flow, schema)
            if item.kind != "text" and item.ref is None:  # a whole-file include keeps the innermost
                item.ref = (doc, node)
            item.via = value
            return item
        return Item("scalar", doc, node, value, flow=flow, uri=uri)

    def merge_sources(
        doc: Doc, node: yaml.Node, k: yaml.Node, v: yaml.Node, stack: tuple, nodes: tuple, schema: Any
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
            out.append((build(doc, source, stack, bool(node.flow_style), nodes, schema), alias))
        return out

    def include(doc: Doc, node: yaml.Node, value: str, stack: tuple, flow: bool, schema: Any) -> Item:
        target: Path | str
        if not value.startswith("file:"):
            target = value
        elif doc.remote:
            target = urllib.parse.urljoin(doc.path, value[5:])  # relative to the remote file
        else:
            target = (doc.path.parent / value[5:]).resolve()
        if target in stack:
            raise ConfigError(f"{doc.path}: include cycle through {target}")
        if is_remote(target):
            is_yaml = urllib.parse.urlsplit(target).path.endswith((".yml", ".yaml"))
        else:
            if not target.is_file():
                raise ConfigError(f"{doc.path}: include target {target} not found")
            is_yaml = target.suffix in (".yml", ".yaml")
        if is_yaml:
            tdoc = get_doc(target)
            if tdoc.remote:
                tdoc.origins += [o for o in doc.origins_of(node) if o not in tdoc.origins]
            item = build(tdoc, tdoc.root, stack + (target,), False, (), schema)
            item.include = target
            item.flow = flow
            return item
        return Item("text", doc, node, text_of(target), include=target, flow=flow)

    root_doc = get_doc(path)
    root = build(root_doc, root_doc.root, (path,), False, (), schema)
    try:
        schemas.validate(schema, root.py(), path.parent / schemas.SCHEMA_FILE)
    except schemas.SchemaProblem as exc:
        raise ConfigError(str(exc)) from exc
    return Config(path, root, docs, schema, fetched, schema_text)


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

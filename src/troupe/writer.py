"""Byte-surgical write-back of configuration changes.

Edits are planned against the original text of each file (see `Doc.edit`) and applied together, so a
refused write never touches a file.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import yaml

from .config import Doc, Item, PathNotFound, WriteRefused
from .yamlcore import (
    Val,
    chomp_for,
    chomp_ok,
    literal_lines,
    literal_ok,
    render_double,
    render_flow,
    render_inline_scalar,
    render_str,
    same,
    style_name,
)

_HEADER = re.compile(r"([|>])([-+]?)([1-9]?)([-+]?)")
_COMMENT_GAP = re.compile(r"[ \t]+(?=#)")


class Writer:
    def __init__(self) -> None:
        self.docs: dict[Path, Doc] = {}
        self.raw: dict[Path, str] = {}  # whole-file writes (text includes)

    def _edit(self, doc: Doc, start: int, end: int, new: str) -> None:
        self.docs[doc.path] = doc
        doc.edit(start, end, new)

    def results(self) -> dict[Path, str]:
        out = {path: doc.edited_text() for path, doc in self.docs.items() if doc.edits}
        out.update(self.raw)
        return out

    # ------------------------------------------------------------------ entry points

    def set_path(self, root: Item, segs: list[str], value: Val, path: str) -> None:
        item = root
        for i, seg in enumerate(segs):
            last = i == len(segs) - 1
            if item.kind == "map":
                if seg in item.value:
                    item = item.value[seg]
                    continue
                rest = segs[i + 1 :]
                if any(s.isdigit() for s in rest):
                    raise PathNotFound(f"path not found: {path}")
                v = value
                for s in reversed(rest):
                    v = Val("map", {s: v})
                self.add_entries(item, [(seg, v)])
                return
            if item.kind == "seq":
                if not seg.isdigit():
                    raise PathNotFound(f"path not found: {path} (not a list index: {seg})")
                idx = int(seg)
                if idx < len(item.value):
                    item = item.value[idx]
                    continue
                if idx == len(item.value) and last:
                    self.append_items(item, [value])
                    return
                raise PathNotFound(f"path not found: {path} (index {idx} out of range)")
            if item is root and item.kind == "scalar" and _empty_doc(item.doc):
                rest = segs[i + 1 :]
                if any(s.isdigit() for s in [seg, *rest]):
                    raise PathNotFound(f"path not found: {path}")
                v = value
                for s in reversed(rest):
                    v = Val("map", {s: v})
                self._append_to_empty_doc(item.doc, [(seg, v)])
                return
            raise PathNotFound(f"path not found: {path} ({'.'.join(segs[:i])} is not a mapping or list)")
        if item.yaml_include:
            raise WriteRefused(f"{path}: value comes from include {item.include}; refusing to replace it")
        self.assign(item, value)

    def assign(self, item: Item, v: Val) -> None:
        if same(item.py(), v.py()):
            return
        if item.yaml_include:
            raise WriteRefused(f"value comes from include {item.include}; refusing to replace it")
        if item.kind == "text":
            self.write_text_include(item, v)
            return
        if item.kind == "map" and v.kind == "map" and v.value:
            kept = [k for k in item.value if k in v.value]
            if kept or not item.value:
                gone = [k for k in item.value if k not in v.value]
                if gone:
                    self.delete_keys(item, gone)
                for k in kept:
                    self.assign(item.value[k], v.value[k])
                new = [(k, x) for k, x in v.value.items() if k not in item.value]
                if new:
                    self.add_entries(item, new)
                return
        if item.kind == "seq" and v.kind == "seq" and v.value:
            n = min(len(item.value), len(v.value))
            if len(v.value) < len(item.value):
                self.truncate(item, len(v.value))
            for i in range(n):
                self.assign(item.value[i], v.value[i])
            if len(v.value) > n:
                self.append_items(item, v.value[n:])
            return
        self.replace(item, v)

    def write_text_include(self, item: Item, v: Val) -> None:
        if v.kind != "str":
            raise WriteRefused(f"{item.include}: a text include can only be set to a string")
        content = v.value
        if "\r\n" in item.value:
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        self.raw[item.include] = content

    # ------------------------------------------------------------------ replacing a value

    def replace(self, item: Item, v: Val) -> None:
        doc, node = item.doc, item.node
        if node is None or item.parent is None:
            raise WriteRefused(f"{doc.path}: cannot replace the document root")
        style = style_name(node)
        if item.flow:
            text = render_flow(v, style if style in ("plain", "single", "double") else None)
            self._replace_inline(doc, node.start_mark.index, node_end(doc, node), text, needs_space=False)
            return

        anchor = anchor_of(item)
        # column of the parent key, or of the item's dash
        pcol = item.key_node.start_mark.column if item.parent.kind == "map" else doc.col(anchor - 1)
        indent = doc.indent
        s = node.start_mark.index
        end = node_end(doc, node)

        # old regions: inline part (on the anchor line) and body (following lines)
        if style in ("literal", "folded"):
            header = _HEADER.match(doc.text, s)
            is_, ie = s, header.end()
            line_end = doc.line_end(doc.line_of(s))
            bs, be = line_end, max(end, line_end)
        elif isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style and node.value:
            if doc.line_of(s) == doc.line_of(anchor):
                is_, ie = s, end
                bs = be = None
            else:
                is_ = ie = anchor
                bs, be = doc.line_end(doc.line_of(anchor)), end
        else:
            is_, ie = s, end
            bs = be = None
        needs_space = is_ == anchor

        # new rendering
        new_inline, body, keep = "", [], False
        if v.is_collection and v.value:
            has_comment = _COMMENT_GAP.match(doc.text, ie, doc.line_end(doc.line_of(ie))) is not None
            if item.parent.kind == "seq" and not (has_comment and doc.line_of(is_) == doc.line_of(ie)):
                # compact form: "- key: value" with the rest aligned under the first entry
                w = max(2, indent)
                sub = block_lines(v, pcol + w, indent)
                is_, needs_space = anchor, False
                new_inline = " " * (w - 1) + doc.newline.join([sub[0][pcol + w :], *sub[1:]])
            else:
                body = block_lines(v, pcol + indent, indent)
        elif v.kind == "str" and style in ("literal", "folded") and literal_ok(v.value):
            hm = _HEADER.match(doc.text, s)
            chomp = hm.group(2) or hm.group(4)
            want, k = chomp_for(v.value)
            if not chomp_ok(chomp, k):
                chomp = want
            ind = hm.group(1)
            new_inline = ind + (chomp + hm.group(3) if hm.group(2) or not hm.group(4) else hm.group(3) + chomp)
            cindent = _content_indent(doc, node, end) or pcol + indent
            if style == "literal":
                body = literal_lines(v.value, cindent)
            else:
                body = folded_lines(doc, node, end, v.value, cindent)
            keep = chomp == "+"
        elif v.multiline and literal_ok(v.value) and not _absorbs_comments(doc, end, pcol + indent):
            chomp, k = chomp_for(v.value)
            new_inline = "|" + chomp
            body = literal_lines(v.value, pcol + indent)
            keep = chomp == "+"
        elif v.is_collection:
            new_inline = "{}" if v.kind == "map" else "[]"
        elif v.multiline:
            new_inline = render_double(v.value)
        else:
            keep_style = style if style in ("plain", "single", "double") else None
            new_inline = render_inline_scalar(v, keep_style, False)

        if bs is None:
            bs = be = doc.line_end(doc.line_of(ie))
        if keep:
            ln = doc.line_of(be)
            while ln + 1 < doc.line_count() and doc.line_text(ln + 1).strip() == "":
                ln += 1
            be = doc.line_end(ln)
        self._replace_inline(doc, is_, ie, new_inline, needs_space, anchor)
        if body or bs != be:
            self._edit(doc, bs, be, "".join(doc.newline + ln for ln in body))

    def _replace_inline(
        self, doc: Doc, is_: int, ie: int, new: str, needs_space: bool, anchor: int | None = None
    ) -> None:
        text = (" " + new) if (new and needs_space) else new
        rest = doc.text[ie : doc.line_end(doc.line_of(ie))]
        m = _COMMENT_GAP.match(rest)
        single_line = doc.line_of(is_) == doc.line_of(ie) and "\n" not in text
        if m and single_line:
            comment_col = doc.col(ie + m.end())
            gap = max(1, comment_col - (doc.col(is_) + len(text)))
            self._edit(doc, is_, ie + m.end(), text + " " * gap)
        elif not text and anchor is not None and not m:
            self._edit(doc, anchor, ie, "")
        else:
            self._edit(doc, is_, ie, text)

    # ------------------------------------------------------------------ adding entries

    def add_entries(self, item: Item, entries: list[tuple[str, Val]]) -> None:
        site = override_site(item)
        if site is not None:
            item, keys = site
            for k in reversed(keys):
                entries = [(k, Val("map", dict(entries)))]
            if item.ref is not None:
                self._add_to_scalar_include(item, entries)
                return
        doc, node = item.doc, item.node
        if node is None:
            self._append_to_empty_doc(doc, entries)
            return
        pairs = node.value
        key_style = style_name(pairs[-1][0]) if pairs else None
        val_style = style_name(pairs[-1][1]) if pairs else None
        if node.flow_style:
            parts = [f"{render_str(k, key_style, True)}: {render_flow(v, val_style)}" for k, v in entries]
            self._flow_insert(doc, node, [v for _, v in pairs], parts)
            return
        col = pairs[0][0].start_mark.column
        lines: list[str] = []
        for k, v in entries:
            head = " " * col + render_str(k, key_style, False) + ":"
            lines += entry_lines(head, v, col, doc.indent, val_style, seq=False)
        self._block_insert(doc, node, col, lines)

    def _add_to_scalar_include(self, item: Item, entries: list[tuple[str, Val]]) -> None:
        """Add overrides to a scalar-form include, converting it to mapping form."""
        doc, node = item.ref
        start, end = node.start_mark.index, node.end_mark.index
        ref = "$ref: " + doc.text[start:end]
        new = Val("map", dict(entries))
        if item.flow:
            self._edit(doc, start, end, "{" + ref + ", " + render_flow(new)[1:])
            return
        lines: list[str] = []
        if node is doc.root:
            col = 0
            self._edit(doc, start, end, ref)
        elif item.parent.kind == "map":
            # "key: file:x  # c" -> "key:  # c", with the mapping below it
            colon = _colon_end(doc, item.key_node, item.key)
            if doc.line_of(start) != doc.line_of(colon):
                raise WriteRefused(f"{doc.path}: cannot convert the include of {item.include} to mapping form")
            col = item.key_node.start_mark.column + doc.indent
            self._edit(doc, colon, end, "")
            lines.append(" " * col + ref)
        else:
            # "- file:x  # c" -> "- $ref: file:x  # c", the rest aligned under it
            dash = _dash_of(doc, node)
            if doc.line_of(start) != doc.line_of(dash):
                raise WriteRefused(f"{doc.path}: cannot convert the include of {item.include} to mapping form")
            w = max(2, doc.indent)
            col = doc.col(dash) + w
            self._edit(doc, dash + 1, start, " " * (w - 1) + "$ref: ")
        lines += block_lines(new, col, doc.indent)
        pos = doc.line_end(doc.line_of(end))
        self._edit(doc, pos, pos, "".join(doc.newline + ln for ln in lines))

    def append_items(self, item: Item, values: list[Val]) -> None:
        doc, node = item.doc, item.node
        val_style = style_name(node.value[-1]) if node.value else None
        if node.flow_style:
            self._flow_insert(doc, node, node.value, [render_flow(v, val_style) for v in values])
            return
        col = doc.col(_dash_of(doc, node.value[0]))
        lines: list[str] = []
        for v in values:
            lines += entry_lines(" " * col + "-", v, col, doc.indent, val_style, seq=True)
        self._block_insert(doc, node, col, lines)

    def _append_to_empty_doc(self, doc: Doc, entries: list[tuple[str, Val]]) -> None:
        lines: list[str] = []
        for k, v in entries:
            lines += entry_lines(render_str(k, None, False) + ":", v, 0, doc.indent, None, seq=False)
        prefix = doc.newline if doc.text and not doc.text.endswith(("\n", "\r")) else ""
        self._edit(doc, len(doc.text), len(doc.text), prefix + "".join(ln + doc.newline for ln in lines))

    def _flow_insert(self, doc: Doc, node: yaml.Node, existing: list[yaml.Node], parts: list[str]) -> None:
        if existing:
            pos = node_end(doc, existing[-1])
            self._edit(doc, pos, pos, "".join(", " + p for p in parts))
        else:
            pos = node.start_mark.index + 1
            self._edit(doc, pos, pos, ", ".join(parts))

    def _block_insert(self, doc: Doc, node: yaml.Node, col: int, lines: list[str]) -> None:
        last = doc.line_of(node_end(doc, node))
        accepted = last
        ln = last + 1
        while ln < doc.line_count():
            t = doc.line_text(ln)
            stripped = t.lstrip(" ")
            if stripped.strip() == "":
                ln += 1
                continue
            if stripped.startswith("#") and len(t) - len(stripped) >= col:
                accepted = ln
                ln += 1
                continue
            break
        nl = doc.newline
        if doc.has_break(accepted):
            pos = doc.line_start(accepted + 1)
            text = "".join(ln + nl for ln in lines)
        else:
            pos = len(doc.text)
            text = "".join(nl + ln for ln in lines)
        self._edit(doc, pos, pos, text)

    # ------------------------------------------------------------------ removing entries

    def delete_keys(self, item: Item, keys: list[str]) -> None:
        if item.parts is not None:
            # A merged mapping: delete each key wherever it is set, in the include and in the overrides.
            for part in item.parts:
                here = [k for k in keys if k in part.value]
                if here:
                    self.delete_keys(part, here)
            return
        node = item.node
        names = [k.value for k, _ in node.value]
        entries = [(k.start_mark.index, node_end(item.doc, v)) for k, v in node.value]
        self._delete_runs(item.doc, node, entries, [n in keys for n in names])

    def truncate(self, item: Item, n: int) -> None:
        doc, node = item.doc, item.node
        if node.flow_style:
            entries = [(v.start_mark.index, node_end(doc, v)) for v in node.value]
        else:
            entries = [(_dash_of(doc, v), node_end(doc, v)) for v in node.value]
        self._delete_runs(doc, node, entries, [i >= n for i in range(len(node.value))])

    def _delete_runs(self, doc: Doc, node: yaml.Node, entries: list[tuple[int, int]], gone: list[bool]) -> None:
        i = 0
        while i < len(entries):
            if not gone[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(entries) and gone[j + 1]:
                j += 1
            start, end = entries[i][0], entries[j][1]
            if node.flow_style:
                if i > 0:
                    self._edit(doc, entries[i - 1][1], end, "")
                else:
                    self._edit(doc, start, entries[j + 1][0], "")
            else:
                ls = doc.line_start(doc.line_of(start))
                if doc.text[ls:start].strip(" ") and j + 1 < len(entries):
                    self._edit(doc, start, entries[j + 1][0], "")  # compact entry after "- "
                else:
                    le = doc.line_of(end)
                    stop = doc.line_start(le + 1) if doc.has_break(le) else len(doc.text)
                    if not doc.has_break(le) and ls > 0:
                        ls = doc.line_end(doc.line_of(ls) - 1)
                    self._edit(doc, ls, stop, "")
            i = j + 1


# ---------------------------------------------------------------------- rendering new block content


def block_lines(v: Val, col: int, indent: int) -> list[str]:
    lines: list[str] = []
    if v.kind == "map":
        for k, x in v.value.items():
            lines += entry_lines(" " * col + render_str(k, None, False) + ":", x, col, indent, None, seq=False)
    else:
        for x in v.value:
            lines += entry_lines(" " * col + "-", x, col, indent, None, seq=True)
    return lines


def entry_lines(head: str, v: Val, col: int, indent: int, val_style: str | None, seq: bool) -> list[str]:
    if v.multiline and literal_ok(v.value):
        chomp, _ = chomp_for(v.value)
        return [head + " |" + chomp] + literal_lines(v.value, col + indent)
    if v.is_collection and v.value:
        if seq:
            w = max(2, indent)
            sub = block_lines(v, col + w, indent)
            return [head + " " * (w - 1) + sub[0][col + w :]] + sub[1:]
        return [head] + block_lines(v, col + indent, indent)
    if v.is_collection:
        return [head + (" {}" if v.kind == "map" else " []")]
    return [head + " " + render_inline_scalar(v, val_style, False)]


def folded_lines(doc: Doc, node: yaml.ScalarNode, end: int, value: str, cindent: int) -> list[str]:
    """Content lines of a folded block scalar for `value`, keeping unchanged paragraphs' bytes."""
    first = doc.line_of(node.start_mark.index) + 1
    last = doc.line_of(end)
    raw = [doc.line_text(ln) for ln in range(first, last + 1)] if end > doc.line_end(first - 1) else []
    # old paragraphs: groups of non-blank lines
    old: list[tuple[str, list[str]]] = []
    group: list[str] = []
    for line in raw + [""]:
        if line.strip() == "":
            if group:
                old.append((" ".join(ln.strip(" ") for ln in group), group))
                group = []
        else:
            group.append(line)
    widths = [len(ln) - min(cindent, len(ln) - len(ln.lstrip(" "))) for _, g in old for ln in g]
    default_width = max(widths, default=80)

    body = value.rstrip("\n")
    k = len(value) - len(body)
    vlines = body.split("\n") if body else []
    # new paragraphs with the number of newlines preceding each
    paras: list[tuple[int, str]] = []
    pending = 0
    for i, vl in enumerate(vlines):
        if vl == "":
            pending += 1
            continue
        if vl[0] in " \t":
            raise WriteRefused(f"{doc.path}: value would put a more-indented line into a folded scalar")
        # k newlines between paragraphs (or before the first) are written as k blank lines
        paras.append((pending + (1 if paras else 0), vl))
        pending = 0

    old_texts = [t for t, _ in old]
    new_texts = [t for _, t in paras]
    plan: list[tuple[str, int | None]] = [("new", None)] * len(new_texts)
    sm = difflib.SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for off, j in enumerate(range(j1, j2)):
            if tag == "equal":
                plan[j] = ("keep", i1 + off)
            elif tag == "replace" and i1 + off < i2:
                plan[j] = ("changed", i1 + off)

    pad = " " * cindent
    out: list[str] = []
    for (sep, text), (how, oi) in zip(paras, plan):
        out += [""] * sep
        if how == "keep":
            out += old[oi][1]
            continue
        if how == "changed":
            width = max(len(ln) - min(cindent, len(ln) - len(ln.lstrip(" "))) for ln in old[oi][1])
        else:
            width = default_width
        out += [pad + ln for ln in _wrap(text, width)]
    out += [""] * max(0, k - 1)
    return out


def _wrap(text: str, width: int) -> list[str]:
    words = re.split(r"(?<=[^ \t]) (?=[^ \t])", text)
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


# ---------------------------------------------------------------------- positions


def override_site(item: Item) -> tuple[Item, list[str]] | None:
    """Where new keys of mapping `item` go when it comes from an included file, and their key path there.

    New keys go into the file that includes it, as overrides: under a merged mapping there, or into the
    scalar-form include (returned with `ref` set). A mapping inside a list item, a mapping in the
    configuration file itself and a merged mapping (whose overrides are already there) take them in place.
    """
    if item.parts is not None:
        return None
    x, keys = item, []
    while x.ref is None:
        parent = x.parent
        if parent is None or parent.kind == "seq":
            return None
        keys.insert(0, x.key)
        if parent.doc is not x.doc:
            return parent, keys
        x = parent
    return x, keys


def _empty_doc(doc: Doc) -> bool:
    return doc.root is None


def node_end(doc: Doc, node: yaml.Node) -> int:
    """End index of a node's content (block scalars: last content line, without its line break)."""
    if isinstance(node, yaml.ScalarNode):
        s, e = node.start_mark.index, node.end_mark.index
        if node.style in ("|", ">"):
            hm = _HEADER.match(doc.text, s)
            if "+" in hm.group(0):
                if doc.text[e - 2 : e] == "\r\n":
                    e -= 2
                elif e > s and doc.text[e - 1] in "\r\n":
                    e -= 1
                return max(e, doc.line_end(doc.line_of(s)))
            region = doc.text[s:e].rstrip()
            return doc.line_end(doc.line_of(s + len(region) - 1))
        return e
    if node.flow_style or not node.value:
        return node.end_mark.index
    last = node.value[-1]
    return node_end(doc, last[1] if isinstance(node, yaml.MappingNode) else last)


def _dash_of(doc: Doc, item_node: yaml.Node) -> int:
    i = item_node.start_mark.index - 1
    while i >= 0 and doc.text[i] in " \t\r\n":
        i -= 1
    if i < 0 or doc.text[i] != "-":
        raise PathNotFound(f"{doc.path}: cannot locate list item")
    return i


def anchor_of(item: Item) -> int:
    """Index just after the ':' of a mapping entry or the '-' of a block sequence item."""
    if item.parent.kind == "map":
        return _colon_end(item.doc, item.key_node, item.key)
    return _dash_of(item.doc, item.node) + 1


def _colon_end(doc: Doc, key_node: yaml.Node, key: str) -> int:
    i = key_node.end_mark.index
    while i < len(doc.text) and doc.text[i] in " \t\r\n":
        i += 1
    if i >= len(doc.text) or doc.text[i] != ":":
        raise PathNotFound(f"{doc.path}: cannot locate value of {key}")
    return i + 1


def _absorbs_comments(doc: Doc, end: int, cindent: int) -> bool:
    """Whether comment lines after `end` would become content of a block scalar indented `cindent`."""
    ln = doc.line_of(end) + 1
    while ln < doc.line_count():
        t = doc.line_text(ln)
        if t.strip() == "":
            ln += 1
            continue
        return t.lstrip(" ").startswith("#") and len(t) - len(t.lstrip(" ")) >= cindent
    return False


def _content_indent(doc: Doc, node: yaml.ScalarNode, end: int) -> int | None:
    first = doc.line_of(node.start_mark.index) + 1
    last = doc.line_of(end)
    for ln in range(first, last + 1):
        t = doc.line_text(ln)
        if t.strip() != "" and end > doc.line_start(ln):
            return len(t) - len(t.lstrip(" "))
    return None


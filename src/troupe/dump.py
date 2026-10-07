"""Rendering a configuration subtree as block-style YAML, optionally annotated with source files."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Callable

from .config import LITERAL, Item, include_like, is_remote
from .yamlcore import chomp_for, literal_lines, literal_ok, render_double, render_str

INDENT = 2

Source = Callable[[Item], str] | None


def yaml_scalar(value: Any) -> str:
    """A non-string scalar as a YAML 1.2 core-schema plain scalar."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return ".nan"
    if math.isinf(value):
        return "-.inf" if value < 0 else ".inf"
    return repr(value)


def _scalar(item: Item, indent: int) -> tuple[str, list[str]]:
    """The inline text of a scalar, and the content lines of a literal block (if it is written as one).

    A string that would be read back as an include is tagged `!literal`, unless the schema marks it a URI.
    """
    text, block = _untagged(item, indent)
    if include_like(item.value) and not item.uri:
        text = f"{LITERAL} {text}"
    return text, block


def _untagged(item: Item, indent: int) -> tuple[str, list[str]]:
    value = item.value
    if not isinstance(value, str):
        return yaml_scalar(value), []
    # A value of only line breaks has no content line to carry them, so it cannot be a literal block.
    if (item.kind == "text" or "\n" in value) and (value.strip("\n") or not value) and literal_ok(value):
        chomp, _ = chomp_for(value)
        return "|" + chomp, literal_lines(value, indent + INDENT)
    if "\n" in value:
        return render_double(value), []
    return render_str(value, None, False), []


def _key(key: str) -> str:
    return render_str(key, None, False)


def _lines(item: Item, indent: int, source: Source) -> list[str]:
    """Lines of a non-empty collection whose entries start at column `indent`."""
    pad = " " * indent
    out: list[str] = []
    entries = item.value.items() if item.kind == "map" else enumerate(item.value)
    for key, child in entries:
        lead = f"{pad}{_key(key)}:" if item.kind == "map" else f"{pad}-"
        if child.kind in ("map", "seq") and child.value:
            nested = _lines(child, indent + INDENT, source)
            if item.kind == "map":
                out.append(lead)
                out.extend(nested)
            else:
                # Compact: the first nested entry goes on the dash line.
                out.append(lead + " " + nested[0][indent + INDENT :])
                out.extend(nested[1:])
        elif child.kind in ("map", "seq"):
            out.append(f"{lead} {'{}' if child.kind == 'map' else '[]'}")
        else:
            text, block = _scalar(child, indent)
            out.append(f"{lead} {text}{_comment(child, source)}")
            out.extend(block)
    return out


def _comment(item: Item, source: Source) -> str:
    return f" # from {source(item)}" if source else ""


def dump(item: Item, source: Source = None) -> str:
    """`item` as a block-style YAML document; `source` names each scalar's file for an end-of-line comment."""
    if item.kind in ("map", "seq") and item.value:
        lines = _lines(item, 0, source)
    elif item.kind in ("map", "seq"):
        lines = ["{}" if item.kind == "map" else "[]"]
    else:
        text, block = _scalar(item, 0)
        lines = [text + _comment(item, source)] + block
    return "".join(line + "\n" for line in lines)


def source_namer(theatre: Path) -> Callable[[Item], str]:
    """Name the file a scalar comes from, relative to the theatre directory, with `/` separators."""
    base = theatre.resolve()

    def name(item: Item) -> str:
        path = item.include if item.kind == "text" else item.doc.path
        if is_remote(path):
            return path
        return Path(os.path.relpath(path, base)).as_posix()

    return name

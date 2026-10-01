"""YAML 1.2 core-schema helpers: scalar resolution, value parsing and scalar rendering."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import yaml

_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_TRUE = re.compile(r"^(?:true|True|TRUE)$")
_FALSE = re.compile(r"^(?:false|False|FALSE)$")
_INT10 = re.compile(r"^[-+]?[0-9]+$")
_INT8 = re.compile(r"^0o[0-7]+$")
_INT16 = re.compile(r"^0x[0-9a-fA-F]+$")
_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_INF = re.compile(r"^[-+]?(?:\.inf|\.Inf|\.INF)$")
_NAN = re.compile(r"^(?:\.nan|\.NaN|\.NAN)$")


def resolve_plain(text: str) -> Any:
    """Resolve a plain scalar using the YAML 1.2 core schema."""
    if _NULL.match(text):
        return None
    if _TRUE.match(text):
        return True
    if _FALSE.match(text):
        return False
    if _INT10.match(text):
        return int(text)
    if _INT8.match(text):
        return int(text[2:], 8)
    if _INT16.match(text):
        return int(text[2:], 16)
    if _FLOAT.match(text):
        return float(text)
    if _INF.match(text):
        return -math.inf if text.startswith("-") else math.inf
    if _NAN.match(text):
        return math.nan
    return text


def scalar_value(node: yaml.ScalarNode) -> Any:
    if node.style is None:
        return resolve_plain(node.value)
    return node.value


def same(a: Any, b: Any) -> bool:
    """Typed structural equality (1 != 1.0 != True)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    return a == b


def format_scalar(value: Any) -> str:
    """Format a scalar for printing by `show`."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return value


# --------------------------------------------------------------------------- values from the command line


@dataclass
class Val:
    """A value given on the command line. `kind` is map, seq, str or other (non-string scalar)."""

    kind: str
    value: Any = None  # dict[str, Val] | list[Val] | str | scalar
    src: str = ""  # source text of a non-string scalar

    def py(self) -> Any:
        if self.kind == "map":
            return {k: v.py() for k, v in self.value.items()}
        if self.kind == "seq":
            return [v.py() for v in self.value]
        return self.value

    @property
    def is_collection(self) -> bool:
        return self.kind in ("map", "seq")

    @property
    def multiline(self) -> bool:
        return self.kind == "str" and "\n" in self.value


class ValueError_(Exception):
    pass


def _to_val(node: yaml.Node) -> Val:
    if isinstance(node, yaml.MappingNode):
        out: dict[str, Val] = {}
        for k, v in node.value:
            if not isinstance(k, yaml.ScalarNode):
                raise ValueError_("mapping keys must be scalars")
            out[k.value] = _to_val(v)
        return Val("map", out)
    if isinstance(node, yaml.SequenceNode):
        return Val("seq", [_to_val(v) for v in node.value])
    value = scalar_value(node)
    if isinstance(value, str):
        return Val("str", value)
    return Val("other", value, src=node.value if node.value != "" else "null")


def parse_value(text: str) -> Val:
    """Parse a command-line VALUE as a single YAML flow node."""
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError_(str(exc).splitlines()[0]) from exc
    if node is None:
        return Val("other", None, src="null")
    if isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style:
        raise ValueError_("value must be a single flow node")
    return _to_val(node)


# --------------------------------------------------------------------------- rendering scalars

_PLAIN_FIRST_BAD = set("-?:,[]{}#&*!|>'\"%@`")
_FLOW_IND = set(",[]{}")


def _has_ctrl(s: str) -> bool:
    return any(
        (ord(c) < 0x20 and c != "\t") or 0x7F <= ord(c) <= 0x9F or c in "\u2028\u2029\ufeff"
        for c in s
    )


def plain_ok(s: str, flow: bool = False) -> bool:
    if not s or s != s.strip(" \t") or "\n" in s or "\t" in s or _has_ctrl(s):
        return False
    if not isinstance(resolve_plain(s), str):
        return False
    if s.startswith(("---", "...")):
        return False
    first = s[0]
    if first in _PLAIN_FIRST_BAD:
        if first not in "-?:" or len(s) < 2 or s[1] == " " or (flow and s[1] in _FLOW_IND):
            return False
    if ": " in s or s.endswith(":") or " #" in s:
        return False
    if flow:
        if any(c in _FLOW_IND for c in s):
            return False
    return True


def single_ok(s: str) -> bool:
    return "\n" not in s and not _has_ctrl(s)


def render_single(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\0": "\\0",
    "\a": "\\a",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\v": "\\v",
    "\f": "\\f",
    "\r": "\\r",
    "\x1b": "\\e",
    "\x85": "\\N",
    "\u2028": "\\L",
    "\u2029": "\\P",
    "\ufeff": "\\uFEFF",
}


def render_double(s: str) -> str:
    out = []
    for c in s:
        if c in _ESCAPES:
            out.append(_ESCAPES[c])
        elif ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F:
            out.append(f"\\x{ord(c):02X}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def style_name(node: yaml.Node | None) -> str | None:
    """Quoting style of a scalar node: plain, single, double, literal, folded."""
    if not isinstance(node, yaml.ScalarNode):
        return None
    return {None: "plain", "'": "single", '"': "double", "|": "literal", ">": "folded"}.get(node.style)


def render_str(s: str, style: str | None, flow: bool) -> str:
    """Render a single-line string in `style` if it can represent it, else the minimal style."""
    if style == "plain" and plain_ok(s, flow):
        return s
    if style == "single" and single_ok(s):
        return render_single(s)
    if style == "double":
        return render_double(s)
    if plain_ok(s, flow):
        return s
    if single_ok(s):
        return render_single(s)
    return render_double(s)


def render_inline_scalar(v: Val, style: str | None, flow: bool) -> str:
    if v.kind == "str":
        if "\n" in v.value:
            return render_double(v.value)
        return render_str(v.value, style, flow)
    return v.src


def render_flow(v: Val, style: str | None = None) -> str:
    if v.kind == "map":
        return "{" + ", ".join(f"{render_str(k, None, True)}: {render_flow(x)}" for k, x in v.value.items()) + "}"
    if v.kind == "seq":
        return "[" + ", ".join(render_flow(x) for x in v.value) + "]"
    return render_inline_scalar(v, style, True)


def chomp_for(value: str) -> tuple[str, int]:
    """Minimal chomping indicator for a block scalar value, and the count of trailing newlines."""
    body = value.rstrip("\n")
    k = len(value) - len(body)
    return ("-" if k == 0 else "" if k == 1 else "+"), k


def chomp_ok(chomp: str, k: int) -> bool:
    return (chomp == "-" and k == 0) or (chomp == "" and k == 1) or (chomp == "+" and k >= 1)


def literal_ok(s: str) -> bool:
    if any(c in s for c in "\r\u2028\u2029\ufeff") or _has_ctrl(s.replace("\n", "")):
        return False
    body = s.rstrip("\n")
    first = next((ln for ln in body.split("\n") if ln.strip(" ") != ""), "")
    if first[:1] in (" ", "\t"):
        return False
    # Leading blank lines longer than the content indentation are not representable.
    for ln in body.split("\n"):
        if ln.strip(" ") != "":
            break
        if ln:
            return False
    return True


def literal_lines(s: str, indent: int) -> list[str]:
    """Content lines of a literal block scalar for value `s` (header chosen separately)."""
    body = s.rstrip("\n")
    k = len(s) - len(body)
    lines = body.split("\n") if body else []
    lines += [""] * max(0, k - 1)
    pad = " " * indent
    return [pad + ln if ln else "" for ln in lines]

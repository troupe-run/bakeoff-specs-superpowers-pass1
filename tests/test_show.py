import math

import pytest

from troupe.config import load
from troupe.yamlcore import same


def show(theatre, *args: str) -> str:
    code, out, err = theatre.run("show", *args)
    assert code == 0, err
    return out


# ---------------------------------------------------------------- get


def test_get_refuses_collections_pointing_to_show(theatre):
    theatre.write("troupe.yml", "m:\n  a: 1\nl: [1]\n")
    code, _, err = theatre.run("get", "m")
    assert code == 5 and "config show" in err
    code, _, err = theatre.run("get", "l")
    assert code == 5 and "config show" in err
    assert theatre.get("m.a") == "1\n"
    assert theatre.run("get")[0] == 2


# ---------------------------------------------------------------- show


def test_show_scalar_like_get(theatre):
    theatre.write("troupe.yml", "s: abc\nq: \"0.2\"\nb: |\n  one\n  two\nn: ~\n")
    for path in ("s", "q", "b", "n"):
        assert show(theatre, path) == theatre.get(path)
    assert theatre.run("show", "nope")[0] == 5
    assert theatre.run("show", "s.x")[0] == 5


def test_show_whole_config_block_style(theatre):
    theatre.write(
        "troupe.yml",
        "# head\nname: x   # c\nm: {a: 1, b: [true, null, 0x10]}\nl:\n- {k: v, j: w}\n- [1, 2]\n- []\ne: {}\n",
    )
    assert show(theatre) == (
        "name: x\n"
        "m:\n"
        "  a: 1\n"
        "  b:\n"
        "    - true\n"
        "    - null\n"
        "    - 16\n"
        "l:\n"
        "  - k: v\n"
        "    j: w\n"
        "  - - 1\n"
        "    - 2\n"
        "  - []\n"
        "e: {}\n"
    )
    assert show(theatre, "m") == "a: 1\nb:\n  - true\n  - null\n  - 16\n"
    assert show(theatre, "l.0") == "k: v\nj: w\n"


def test_show_quotes_what_needs_quoting(theatre, tmp_path):
    theatre.write(
        "troupe.yml",
        "a: '0.2'\nb: 'true'\nc: 'x: y'\nd: \"tab\\there\"\ne: .nan\nf: -.inf\ng: 1.5\n"
        "h: \"x\\r\\ny\"\n'1': k\n\"k: v\": 2\ni: \"\\n\"\n",
    )
    out = show(theatre)
    assert out == (
        "a: '0.2'\n"
        "b: 'true'\n"
        "c: 'x: y'\n"
        "d: 'tab\there'\n"
        "e: .nan\n"
        "f: -.inf\n"
        "g: 1.5\n"
        "h: \"x\\r\\ny\"\n"
        "'1': k\n"
        "'k: v': 2\n"
        "i: \"\\n\"\n"
    )
    expected = load(theatre.root).root.py()
    loaded = load_text(tmp_path, out)
    assert math.isnan(loaded.pop("e")) and math.isnan(expected.pop("e"))
    assert same(loaded, expected)


def load_text(tmp_path, text):
    other = tmp_path / "other"
    (other / ".troupe").mkdir(parents=True)
    (other / ".troupe" / "troupe.yml").write_bytes(text.encode("utf-8"))
    return load(other).root.py()


def test_show_resolves_includes_text_as_literal(theatre, tmp_path):
    theatre.write("troupe.yml", "agents:\n  coder: file:agents/coder.yaml\nprompt: file:p/sys.md\nmulti: \"a\\nb\\n\"\n")
    theatre.write("agents/coder.yaml", "model: x\nprompt: file:../p/c.txt\nlist: [file:../p/one.txt]\n")
    theatre.write("p/sys.md", "hello\nworld")
    theatre.write("p/c.txt", "code\n\n")
    theatre.write("p/one.txt", "one")
    out = show(theatre)
    assert out == (
        "agents:\n"
        "  coder:\n"
        "    model: x\n"
        "    prompt: |+\n"
        "      code\n"
        "\n"
        "    list:\n"
        "      - |-\n"
        "        one\n"
        "prompt: |-\n"
        "  hello\n"
        "  world\n"
        "multi: |\n"
        "  a\n"
        "  b\n"
    )
    assert same(load_text(tmp_path, out), load(theatre.root).root.py())
    assert show(theatre, "agents.coder") == "model: x\nprompt: |+\n  code\n\nlist:\n  - |-\n    one\n"


def test_show_text_include_not_representable_as_literal(theatre):
    theatre.write("troupe.yml", "a: file:crlf.txt\nb: file:lead.txt\nc: file:empty.txt\n")
    theatre.write("crlf.txt", "x\r\ny\r\n")
    theatre.write("lead.txt", "  indented\n")
    theatre.write("empty.txt", "")
    assert show(theatre) == 'a: "x\\r\\ny\\r\\n"\nb: "  indented\\n"\nc: |-\n'


# ---------------------------------------------------------------- show --sources


def test_show_sources(theatre, tmp_path):
    theatre.write("troupe.yml", "agents:\n  coder: file:agents/coder.yaml\nprompt: file:p/sys.md\nn: 1\ne: []\n")
    theatre.write("agents/coder.yaml", "model: x\nprompt: file:../p/c.txt\ntools:\n  - a\n  - {b: 2}\n")
    theatre.write("p/sys.md", "hello\nworld\n")
    theatre.write("p/c.txt", "code")
    out = show(theatre, "--sources")
    assert out == (
        "agents:\n"
        "  coder:\n"
        "    model: x # from .troupe/agents/coder.yaml\n"
        "    prompt: |- # from .troupe/p/c.txt\n"
        "      code\n"
        "    tools:\n"
        "      - a # from .troupe/agents/coder.yaml\n"
        "      - b: 2 # from .troupe/agents/coder.yaml\n"
        "prompt: | # from .troupe/p/sys.md\n"
        "  hello\n"
        "  world\n"
        "n: 1 # from .troupe/troupe.yml\n"
        "e: []\n"
    )
    assert same(load_text(tmp_path, out), load(theatre.root).root.py())
    assert show(theatre, "agents.coder.tools", "--sources") == (
        "- a # from .troupe/agents/coder.yaml\n- b: 2 # from .troupe/agents/coder.yaml\n"
    )
    assert show(theatre, "n", "--sources") == "1 # from .troupe/troupe.yml\n"
    assert show(theatre, "prompt", "--sources") == "| # from .troupe/p/sys.md\n  hello\n  world\n"


def test_show_sources_outside_theatre_dir(theatre, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-shared")
    outside.mkdir()
    (outside / "s.yml").write_text("k: v\n")
    theatre.write("troupe.yml", f"s: file:../../{outside.name}/s.yml\n")
    assert show(theatre, "--sources") == f"s:\n  k: v # from ../{outside.name}/s.yml\n"


def test_show_reconciles(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.get("a")
    theatre.write("troupe.yml", "a: 2\n")
    show(theatre)
    assert [e["op"] for e in theatre.history()] == ["import", "set"]


@pytest.mark.parametrize("text", ["", "5\n", "[]\n"])
def test_show_root_not_a_mapping(theatre, text):
    theatre.write("troupe.yml", text)
    assert show(theatre) == {"": "null\n", "5\n": "5\n", "[]\n": "[]\n"}[text]

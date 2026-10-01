import pytest


def check_set(theatre, before: str, path: str, value: str, after: str, rel: str = "troupe.yml"):
    theatre.write(rel, before)
    theatre.set(path, value)
    assert theatre.read(rel) == after


# ---------------------------------------------------------------- loading & show


def test_show_scalars(theatre):
    theatre.write(
        "troupe.yml",
        "s: abc\nf: 0.2\nb: true\nn: null\ni: 0x1F\nq: \"0.2\"\nl:\n  - x\n  - y\ne: 1e3\nm: {a: 1}\nt: ~\n",
    )
    assert theatre.show("s") == "abc\n"
    assert theatre.show("f") == "0.2\n"
    assert theatre.show("b") == "true\n"
    assert theatre.show("n") == "null\n"
    assert theatre.show("t") == "null\n"
    assert theatre.show("i") == "31\n"
    assert theatre.show("q") == "0.2\n"
    assert theatre.show("l.1") == "y\n"
    assert theatre.show("e") == "1000.0\n"
    assert theatre.show("m.a") == "1\n"
    assert theatre.run("show", "l")[0] == 5
    assert theatre.run("show", "m")[0] == 5
    assert theatre.run("show", "nope")[0] == 5
    assert theatre.run("show", "l.5")[0] == 5
    assert theatre.run("show", "s.x")[0] == 5


def test_show_block_string(theatre):
    theatre.write("troupe.yml", "a: |\n  one\n  two\nb: |-\n  x\n")
    assert theatre.show("a") == "one\ntwo\n"
    assert theatre.show("b") == "x\n"


def test_missing_or_both_config(theatre):
    assert theatre.run("show", "a")[0] == 4
    theatre.write("troupe.yml", "a: 1\n")
    theatre.write("troupe.yaml", "a: 1\n")
    assert theatre.run("show", "a")[0] == 4


def test_yaml_config_name(theatre):
    theatre.write("troupe.yaml", "a: 1\n")
    assert theatre.show("a") == "1\n"


def test_includes(theatre):
    theatre.write("troupe.yml", "agents:\n  coder: file:agents/coder.yaml\nprompt: file:p/sys.md\n")
    theatre.write("agents/coder.yaml", "model: x\nprompt: file:../p/c.txt\n")
    theatre.write("p/sys.md", "hello\r\nworld")
    theatre.write("p/c.txt", "code\n")
    assert theatre.show("agents.coder.model") == "x\n"
    assert theatre.show("agents.coder.prompt") == "code\n"
    assert theatre.show("prompt") == "hello\r\nworld\n"
    assert theatre.run("show", "agents.coder")[0] == 5


def test_include_missing_and_cycle(theatre):
    theatre.write("troupe.yml", "a: file:x.yml\n")
    assert theatre.run("show", "a")[0] == 4
    theatre.write("x.yml", "b: file:troupe.yml\n")
    assert theatre.run("show", "a")[0] == 4
    assert not (theatre.dir / "troupe-state.sqlite").exists()


def test_invalid_yaml(theatre):
    theatre.write("troupe.yml", "a: [1\n")
    assert theatre.run("show", "a")[0] == 4


# ---------------------------------------------------------------- set: scalars


def test_set_keeps_everything_else(theatre):
    check_set(
        theatre,
        "# head\na:   1   # c1\nb: 'x'  # c2\nc: \"y\"\n",
        "a",
        "12345",
        "# head\na:   12345 # c1\nb: 'x'  # c2\nc: \"y\"\n",
    )


def test_comment_moves_back(theatre):
    check_set(theatre, "a: longvalue  # c\n", "a", "x", "a: x          # c\n")


def test_quoting_kept_or_fallback(theatre):
    theatre.write("troupe.yml", "a: 'x'\nb: \"y\"\nc: z\nd: 5\n")
    theatre.set("a", "it's")
    theatre.set("b", "q\"uote")
    theatre.set("c", '"0.2"')
    theatre.set("d", "hello")
    assert theatre.read() == "a: 'it''s'\nb: \"q\\\"uote\"\nc: '0.2'\nd: hello\n"
    theatre.set("c", '"a: b"')
    assert "c: 'a: b'\n" in theatre.read()
    theatre.set("a", "7")
    assert "a: 7\n" in theatre.read()
    assert theatre.show("a") == "7\n"


def test_double_quoted_escapes_only_required(theatre):
    check_set(theatre, 'a: "x"\n', "a", '"tab\\there é ü"', 'a: "tab\\there é ü"\n')


def test_multiline_replaces_plain_with_literal(theatre):
    check_set(theatre, "a: x  # c\nb: 1\n", "a", '"one\\ntwo\\n"', "a: |  # c\n  one\n  two\nb: 1\n")
    check_set(theatre, "a: x\n", "a", '"one\\ntwo"', "a: |-\n  one\n  two\n")
    check_set(theatre, "l:\n  - x\n", "l.0", '"one\\ntwo\\n"', "l:\n  - |\n    one\n    two\n")


def test_literal_keeps_indicator_and_adjusts_chomping(theatre):
    check_set(theatre, "a: |\n    one\n    two\nb: 1\n", "a", '"three\\n"', "a: |\n    three\nb: 1\n")
    check_set(theatre, "a: |\n  one\nb: 1\n", "a", '"x\\ny"', "a: |-\n  x\n  y\nb: 1\n")
    check_set(theatre, "a: |-\n  one\n\nb: 1\n", "a", '"x\\n\\n"', "a: |+\n  x\n\nb: 1\n")
    check_set(theatre, "a: |+\n  one\n\n\nb: 1\n", "a", '"x\\n"', "a: |+\n  x\nb: 1\n")


def test_block_scalar_to_plain(theatre):
    check_set(theatre, "a: |\n  one\n  two\nb: 1\n", "a", "5", "a: 5\nb: 1\n")


def test_folded_rewrap(theatre):
    before = (
        "notes: >\n"
        "  The quick brown fox\n"
        "  jumps over the lazy dog.\n"
        "\n"
        "  Second paragraph stays.\n"
        "next: 1\n"
    )
    theatre.write("troupe.yml", before)
    theatre.set("notes", '"The quick brown fox jumps over the very lazy dog.\\nSecond paragraph stays.\\n"')
    assert theatre.read() == (
        "notes: >\n"
        "  The quick brown fox\n"
        "  jumps over the very lazy\n"
        "  dog.\n"
        "\n"
        "  Second paragraph stays.\n"
        "next: 1\n"
    )
    assert theatre.show("notes") == "The quick brown fox jumps over the very lazy dog.\nSecond paragraph stays.\n"


def test_folded_keeps_unchanged_paragraph_bytes(theatre):
    before = "n: >-\n  aaa bbb\n  ccc\n\n  dd\n"
    theatre.write("troupe.yml", before)
    theatre.set("n", '"aaa bbb ccc\\ndd ee ff gg"')
    assert theatre.read() == "n: >-\n  aaa bbb\n  ccc\n\n  dd\n  ee\n  ff\n  gg\n"


def test_folded_new_paragraph_uses_longest_line(theatre):
    theatre.write("troupe.yml", "n: >\n  aaaa bbbb\n  cc\n")
    theatre.set("n", '"aaaa bbbb cc\\nxx yy zz ww vv\\n"')
    assert theatre.read() == "n: >\n  aaaa bbbb\n  cc\n\n  xx yy zz\n  ww vv\n"


def test_folded_more_indented_refused(theatre):
    theatre.write("troupe.yml", "n: >\n  aaa\n")
    theatre.set("n", '"aaa\\n  indented\\n"', expect=3)
    assert theatre.read() == "n: >\n  aaa\n"


def test_crlf_file(theatre):
    check_set(theatre, "a: 1\r\nb:\r\n  c: 2\r\n", "b.d", "3", "a: 1\r\nb:\r\n  c: 2\r\n  d: 3\r\n")
    check_set(theatre, "a: x\r\n", "a", '"p\\nq\\n"', "a: |\r\n  p\r\n  q\r\n")


def test_unicode_columns(theatre):
    check_set(theatre, "a: é   # c\n", "a", "éé", "a: éé  # c\n")


# ---------------------------------------------------------------- set: new keys and items


def test_new_key_after_trailing_comments(theatre):
    check_set(
        theatre,
        "a:\n  b: 1\n  # trailing b comment\n\n# top comment\nz: 2\n",
        "a.c",
        "3",
        "a:\n  b: 1\n  # trailing b comment\n  c: 3\n\n# top comment\nz: 2\n",
    )


def test_new_key_copies_sibling_style(theatre):
    check_set(theatre, 'a:\n  "b": "x"\n', "a.c", "y", 'a:\n  "b": "x"\n  "c": "y"\n')
    check_set(theatre, "a:\n  b: 1\n", "a.c", '"true"', "a:\n  b: 1\n  c: 'true'\n")


def test_new_nested_keys(theatre):
    check_set(theatre, "a:\n    b: 1\nz: 0\n", "a.c.d.e", "v", "a:\n    b: 1\n    c:\n        d:\n            e: v\nz: 0\n")
    check_set(theatre, "a: 1\n", "b.c", "v", "a: 1\nb:\n  c: v\n")


def test_new_key_no_trailing_newline(theatre):
    check_set(theatre, "a: 1", "b", "2", "a: 1\nb: 2")


def test_new_key_collection_value(theatre):
    check_set(theatre, "a: 1\n", "m", "{x: 1, y: [p, q]}", "a: 1\nm:\n  x: 1\n  y:\n    - p\n    - q\n")
    check_set(theatre, "a: 1\n", "e", "{}", "a: 1\ne: {}\n")


def test_new_key_multiline(theatre):
    check_set(theatre, "a: 1\n", "t", '"x\\ny\\n"', "a: 1\nt: |\n  x\n  y\n")
    check_set(theatre, "a: {b: 1}\n", "a.t", '"x\\ny\\n"', 'a: {b: 1, t: "x\\ny\\n"}\n')


def test_flow_collections(theatre):
    check_set(theatre, "a: {b: 1}  # c\n", "a.c", "2", "a: {b: 1, c: 2}  # c\n")
    check_set(theatre, "a: [x, y]\n", "a.2", "z", "a: [x, y, z]\n")
    check_set(theatre, "a: []\n", "a.0", "z", "a: [z]\n")
    check_set(theatre, "a: {}\n", "a.b", "{c: 1}", "a: {b: {c: 1}}\n")
    check_set(theatre, "a: [x, 'y']\n", "a.2", "z", "a: [x, 'y', 'z']\n")
    check_set(theatre, "a: {b: 1}\n", "a.b", "[1, 2]", "a: {b: [1, 2]}\n")


def test_append_block_list(theatre):
    check_set(theatre, "l:\n- a\n- b\n# end\nx: 1\n", "l.2", "c", "l:\n- a\n- b\n# end\n- c\nx: 1\n")
    check_set(theatre, "l:\n  - a\n", "l.1", "{k: v, w: 2}", "l:\n  - a\n  - k: v\n    w: 2\n")
    theatre.set("l.5", "x", expect=5)
    theatre.set("l.x", "x", expect=5)


def test_set_through_scalar_is_5(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.set("a.b", "2", expect=5)
    assert theatre.read() == "a: 1\n"


# ---------------------------------------------------------------- set: collections


def test_scalar_to_block_collection(theatre):
    check_set(theatre, "a: 1  # keep\nb: 2\n", "a", "{x: 1}", "a:    # keep\n  x: 1\nb: 2\n")
    check_set(theatre, "a: 1\nb: 2\n", "a", "[p, q]", "a:\n  - p\n  - q\nb: 2\n")


def test_collection_to_scalar(theatre):
    check_set(theatre, "a:\n  x: 1\n  y: 2\nb: 2\n", "a", "5", "a: 5\nb: 2\n")


def test_collection_merge(theatre):
    check_set(
        theatre,
        "a:\n  x: 1  # c\n  y: 2\n  z: 3\nb: 2\n",
        "a",
        "{x: 1, z: 4, w: 5}",
        "a:\n  x: 1  # c\n  z: 4\n  w: 5\nb: 2\n",
    )
    check_set(theatre, "l:\n  - a\n  - b\n  - c\n", "l", "[a, x]", "l:\n  - a\n  - x\n")
    check_set(theatre, "l: [a, b, c]\n", "l", "[a]", "l: [a]\n")


def test_include_refused(theatre):
    theatre.write("troupe.yml", "agents:\n  coder: file:coder.yaml\n")
    theatre.write("coder.yaml", "model: x\n")
    theatre.set("agents.coder", "{model: y}", expect=3)
    theatre.set("agents.coder.model", "y")
    theatre.set("agents.coder.temp", "0.5")
    assert theatre.read("coder.yaml") == "model: y\ntemp: 0.5\n"
    assert theatre.read() == "agents:\n  coder: file:coder.yaml\n"


def test_text_include_write(theatre):
    theatre.write("troupe.yml", "p: file:p.md\nq: file:q.md\n")
    theatre.write("p.md", "old\r\ntext\r\n")
    theatre.write("q.md", "old\n")
    theatre.set("p", '"new\\nlines\\n"')
    theatre.set("q", "brand new")
    assert theatre.read("p.md") == "new\r\nlines\r\n"
    assert theatre.read("q.md") == "brand new"
    theatre.set("q", "5", expect=3)
    assert theatre.read("q.md") == "brand new"


def test_usage_errors(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    assert theatre.run("set", "a")[0] == 2
    assert theatre.run("set", "a..b", "1")[0] == 2
    assert theatre.run("set", "a", "[1")[0] == 2


def test_noop_set(theatre):
    theatre.write("troupe.yml", "a: 1.00\n")
    theatre.set("a", "1.0")
    assert theatre.read() == "a: 1.00\n"
    assert [e["op"] for e in theatre.history()] == ["import"]


# ---------------------------------------------------------------- history


def test_history(theatre):
    theatre.write("troupe.yml", "a: 1\nm:\n  x: 1\n  y: [p, q]\nt: file:t.txt\n")
    theatre.write("t.txt", "hi\n")
    assert theatre.history() == [
        {"seq": 1, "op": "import", "path": "a", "old": None, "new": 1},
        {"seq": 2, "op": "import", "path": "m.x", "old": None, "new": 1},
        {"seq": 3, "op": "import", "path": "m.y.0", "old": None, "new": "p"},
        {"seq": 4, "op": "import", "path": "m.y.1", "old": None, "new": "q"},
        {"seq": 5, "op": "import", "path": "t", "old": None, "new": "hi\n"},
    ]
    theatre.set("m", "{x: 2}")
    h = theatre.history("m")
    assert h[-3:] == [
        {"seq": 6, "op": "set", "path": "m.x", "old": 1, "new": 2},
        {"seq": 7, "op": "remove", "path": "m.y.0", "old": "p", "new": None},
        {"seq": 8, "op": "remove", "path": "m.y.1", "old": "q", "new": None},
    ]
    assert theatre.history("m.x") == [h[0], h[3]]
    assert theatre.history("a") == [{"seq": 1, "op": "import", "path": "a", "old": None, "new": 1}]
    assert theatre.history("m.y.0") == [h[1], h[4]]


def test_import_is_not_reconciling(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: 2\n")
    assert theatre.show("a") == "2\n"
    assert len(theatre.history()) == 1


def test_cli_entrypoint(tmp_path):
    import subprocess
    import sys

    (tmp_path / ".troupe").mkdir()
    (tmp_path / ".troupe" / "troupe.yml").write_text("a: 1\n")
    r = subprocess.run(
        [sys.executable, "-m", "troupe.cli", "config", "show", "a", "--theatre", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and r.stdout == "1\n"

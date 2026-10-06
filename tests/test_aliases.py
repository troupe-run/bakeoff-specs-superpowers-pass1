SHARED = "defaults: &d\n  model: opus\ncoder:\n  settings: *d\nreviewer:\n  settings: *d\n"


def test_set_at_anchor_changes_every_alias(theatre):
    theatre.write("troupe.yml", SHARED)
    theatre.set("defaults.model", "sonnet")
    assert theatre.read() == SHARED.replace("opus", "sonnet")
    assert theatre.get("coder.settings.model") == "sonnet\n"
    assert theatre.get("reviewer.settings.model") == "sonnet\n"


def test_set_anchored_scalar_keeps_anchor(theatre):
    theatre.write("troupe.yml", "x: &s opus  # c\ny: *s\nz: !!str &t a\nw: *t\n")
    theatre.set("x", "haiku")
    theatre.set("z", "b")
    assert theatre.read() == "x: &s haiku # c\ny: *s\nz: !!str &t b\nw: *t\n"
    assert theatre.get("y") == "haiku\n"
    assert theatre.get("w") == "b\n"


def test_set_anchored_collection_keeps_anchor(theatre):
    theatre.write("troupe.yml", "a: &l [1, 2]\nb: *l\n")
    theatre.set("a", "[3]")
    assert theatre.read() == "a: &l [3]\nb: *l\n"


def test_set_through_alias_is_refused(theatre):
    theatre.write("troupe.yml", SHARED)
    for path, value in [
        ("coder.settings.model", "sonnet"),
        ("coder.settings.new", "1"),
        ("coder.settings", "{model: sonnet}"),
        ("coder.settings", "1"),
        ("coder", "{settings: {model: sonnet}}"),
    ]:
        code, _, err = theatre.run("set", path, value)
        assert code == 3, path
        assert "--expand-alias" in err
    assert theatre.read() == SHARED


def test_set_through_alias_refused_even_if_unchanged(theatre):
    theatre.write("troupe.yml", SHARED)
    code, _, err = theatre.run("set", "coder.settings.model", "opus")
    assert code == 3 and "--expand-alias" in err


def test_expand_alias(theatre):
    theatre.write("troupe.yml", SHARED)
    theatre.set("coder.settings.model", "sonnet", "--expand-alias")
    assert theatre.read() == (
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n    model: sonnet\n"
        "reviewer:\n  settings: *d\n"
    )
    assert theatre.get("reviewer.settings.model") == "opus\n"
    assert theatre.get("defaults.model") == "opus\n"
    hist = [(e["path"], e["new"]) for e in theatre.history() if e["op"] == "set"]
    assert hist == [("coder.settings.model", "sonnet")]


def test_expand_alias_new_key(theatre):
    theatre.write("troupe.yml", SHARED)
    theatre.set("coder.settings.t", "0.3", "--expand-alias")
    assert theatre.read() == (
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n    model: opus\n"
        "    t: 0.3\nreviewer:\n  settings: *d\n"
    )


def test_expand_alias_keeps_trailing_comment(theatre):
    theatre.write("troupe.yml", "d: &d\n  m: 1\nc: *d  # mine\n")
    theatre.set("c.m", "2", "--expand-alias")
    assert theatre.read() == "d: &d\n  m: 1\nc:  # expanded from *d by troupe  # mine\n  m: 2\n"


def test_expand_alias_scalar(theatre):
    theatre.write("troupe.yml", "x: &s opus\ny: *s\nz: *s\n")
    theatre.set("y", "sonnet", "--expand-alias")
    assert theatre.read() == "x: &s opus\ny: sonnet  # expanded from *s by troupe\nz: *s\n"


def test_expand_alias_whole_value(theatre):
    theatre.write("troupe.yml", SHARED)
    theatre.set("coder.settings", "{model: haiku, t: 1}", "--expand-alias")
    assert theatre.read() == (
        "defaults: &d\n  model: opus\ncoder:\n  settings:  # expanded from *d by troupe\n    model: haiku\n"
        "    t: 1\nreviewer:\n  settings: *d\n"
    )


def test_expand_alias_list_item(theatre):
    theatre.write("troupe.yml", "d: &d\n  m: 1\nl:\n  - *d\n  - *d\n")
    theatre.set("l.0.m", "2", "--expand-alias")
    assert theatre.read() == "d: &d\n  m: 1\nl:\n  -  # expanded from *d by troupe\n    m: 2\n  - *d\n"
    assert theatre.get("l.1.m") == "1\n"


def test_expand_alias_copies_nested_content(theatre):
    theatre.write("troupe.yml", "d: &d\n  m: 1\n  p:\n    q: [a, b]\n    r: 'x y'\nc: *d\n")
    theatre.set("c.p.q.0", "z", "--expand-alias")
    assert theatre.read() == (
        "d: &d\n  m: 1\n  p:\n    q: [a, b]\n    r: 'x y'\n"
        "c:  # expanded from *d by troupe\n  m: 1\n  p:\n    q:\n      - z\n      - b\n    r: x y\n"
    )


def test_expand_alias_in_flow_is_refused(theatre):
    text = "d: &d {m: 1}\nc: {s: *d}\n"
    theatre.write("troupe.yml", text)
    theatre.set("c.s.m", "2", "--expand-alias", expect=3)
    assert theatre.read() == text


def test_expand_alias_without_alias_is_a_plain_set(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.set("a", "2", "--expand-alias")
    assert theatre.read() == "a: 2\n"


MERGED = "base: &b\n  model: opus\n  t: 1\ncoder:\n  <<: *b\n  t: 2\n"


def test_merge_keys_are_read(theatre):
    theatre.write("troupe.yml", MERGED)
    assert theatre.get("coder.model") == "opus\n"
    assert theatre.get("coder.t") == "2\n"
    code, out, _ = theatre.run("show", "coder")
    assert out == "model: opus\nt: 2\n"


def test_merge_keys_are_never_written(theatre):
    theatre.write("troupe.yml", MERGED)
    # An explicit key beside the merge is an ordinary value.
    theatre.set("coder.t", "3")
    assert theatre.read() == MERGED.replace("t: 2", "t: 3")
    # A merged value is reached through the alias.
    code, _, err = theatre.run("set", "coder.model", "sonnet")
    assert code == 3 and "--expand-alias" in err
    theatre.set("coder.<<", "{}", expect=3)
    # Removing a merged key would mean writing the merge key.
    theatre.set("coder", "{t: 3}", expect=3)
    assert theatre.read() == MERGED.replace("t: 2", "t: 3")
    # A new key goes beside the merge key.
    theatre.set("coder.new", "x")
    assert theatre.read() == MERGED.replace("t: 2", "t: 3\n  new: x")


def test_expand_merged_value_adds_explicit_key(theatre):
    theatre.write("troupe.yml", MERGED + "other:\n  <<: *b\n")
    theatre.set("coder.model", "sonnet", "--expand-alias")
    assert theatre.read() == MERGED + "  model: sonnet  # expanded from *b by troupe\nother:\n  <<: *b\n"
    assert theatre.get("other.model") == "opus\n"
    assert theatre.get("base.model") == "opus\n"


def test_expand_merged_mapping_copies_it(theatre):
    theatre.write("troupe.yml", "b: &b\n  p:\n    m: 1\n    n: 2\nc:\n  <<: *b\n  t: 1\n")
    theatre.set("c.p.m", "5", "--expand-alias")
    assert theatre.read() == (
        "b: &b\n  p:\n    m: 1\n    n: 2\nc:\n  <<: *b\n  t: 1\n  p:  # expanded from *b by troupe\n"
        "    m: 5\n    n: 2\n"
    )


def test_merge_of_inline_mapping_is_not_expanded(theatre):
    text = "c:\n  <<: {m: 1}\n"
    theatre.write("troupe.yml", text)
    theatre.set("c.m", "2", "--expand-alias", expect=3)
    assert theatre.read() == text


def test_expand_alias_resolves_merges_in_copy(theatre):
    theatre.write("troupe.yml", "b: &b\n  m: 1\nd: &d\n  <<: *b\n  n: 2\nc: *d\n")
    theatre.set("c.n", "3", "--expand-alias")
    assert theatre.read() == (
        "b: &b\n  m: 1\nd: &d\n  <<: *b\n  n: 2\nc:  # expanded from *d by troupe\n  m: 1\n  n: 3\n"
    )


def test_recursive_alias_is_a_config_error(theatre):
    theatre.write("troupe.yml", "a: &a\n  b: *a\n")
    code, _, err = theatre.run("show")
    assert code == 4 and "recursive alias" in err

import pytest


def test_new_key_becomes_override_in_including_file(theatre):
    theatre.write("troupe.yml", "agents:\n  coder: file:agents/coder.yaml  # c\n  other: 1\n")
    theatre.write("agents/coder.yaml", "model: x\n")
    theatre.set("agents.coder.temperature", "0.3")
    assert theatre.read("agents/coder.yaml") == "model: x\n"
    assert theatre.read() == (
        "agents:\n  coder:  # c\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n  other: 1\n"
    )
    assert theatre.get("agents.coder.temperature") == "0.3\n"
    assert theatre.get("agents.coder.model") == "x\n"
    # A second new key joins the existing overrides.
    theatre.set("agents.coder.top_p", "0.9")
    assert theatre.read() == (
        "agents:\n  coder:  # c\n    $ref: file:agents/coder.yaml\n    temperature: 0.3\n    top_p: 0.9\n"
        "  other: 1\n"
    )
    assert [e["path"] for e in theatre.history() if e["op"] == "set"] == [
        "agents.coder.temperature",
        "agents.coder.top_p",
    ]


def test_conversion_uses_file_indentation_and_quoting(theatre):
    theatre.write("troupe.yml", "agents:\n    coder: 'file:c.yml'\n")
    theatre.write("c.yml", "model: x\n")
    theatre.set("agents.coder.t", "1")
    assert theatre.read() == "agents:\n    coder:\n        $ref: 'file:c.yml'\n        t: 1\n"


def test_new_nested_key_under_included_section(theatre):
    theatre.write("troupe.yml", "coder: file:c.yml\n")
    theatre.write("c.yml", "params:\n  a: 1\n  inner:\n    b: 2\n")
    theatre.set("coder.params.inner.c", "3")
    assert theatre.read("c.yml") == "params:\n  a: 1\n  inner:\n    b: 2\n"
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  params:\n    inner:\n      c: 3\n"
    assert theatre.get("coder.params.a") == "1\n"
    assert theatre.get("coder.params.inner.b") == "2\n"
    # The overrides now hold `params`: the next new key goes beside it.
    theatre.set("coder.params.d", "4")
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  params:\n    inner:\n      c: 3\n    d: 4\n"
    # A new key several levels deep is written as nested mappings.
    theatre.set("coder.x.y", "z")
    assert theatre.read().endswith("    d: 4\n  x:\n    y: z\n")
    assert theatre.read("c.yml") == "params:\n  a: 1\n  inner:\n    b: 2\n"


def test_mapping_form_include_is_deep_merged(theatre):
    theatre.write(
        "troupe.yml",
        "coder:\n  $ref: file:c.yml\n  model: y\n  params:\n    a: 10\n  tools: [z]\n",
    )
    theatre.write("c.yml", "model: x\nparams:\n  a: 1\n  b: 2\ntools: [p, q]\nkeep: k\n")
    assert theatre.get("coder.model") == "y\n"
    assert theatre.get("coder.params.a") == "10\n"
    assert theatre.get("coder.params.b") == "2\n"
    assert theatre.get("coder.keep") == "k\n"
    code, out, _ = theatre.run("show", "coder.tools")
    assert out == "- z\n"


def test_set_existing_values_where_they_live(theatre):
    theatre.write("troupe.yml", "coder:\n  $ref: file:c.yml\n  model: y\n")
    theatre.write("c.yml", "model: x\ntemp: 1\n")
    theatre.set("coder.model", "w")  # overridden: the override changes
    theatre.set("coder.temp", "2")  # not overridden: changed in the included file
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  model: w\n"
    assert theatre.read("c.yml") == "model: x\ntemp: 2\n"
    theatre.set("coder.new", "n")
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  model: w\n  new: n\n"


def test_list_items_stay_in_the_file_holding_the_list(theatre):
    theatre.write("troupe.yml", "coder: file:c.yml\n")
    theatre.write("c.yml", "tools:\n  - name: a\n")
    theatre.set("coder.tools.1", "b")
    theatre.set("coder.tools.0.mode", "fast")
    assert theatre.read() == "coder: file:c.yml\n"
    assert theatre.read("c.yml") == "tools:\n  - name: a\n    mode: fast\n  - b\n"


def test_include_as_list_item_is_converted(theatre):
    theatre.write("troupe.yml", "agents:\n  - file:c.yml  # c\n  - other\n")
    theatre.write("c.yml", "model: x\n")
    theatre.set("agents.0.t", "1")
    assert theatre.read("c.yml") == "model: x\n"
    assert theatre.read() == "agents:\n  - $ref: file:c.yml  # c\n    t: 1\n  - other\n"
    assert theatre.get("agents.0.model") == "x\n"


def test_include_in_flow_collection_is_converted(theatre):
    theatre.write("troupe.yml", "agents: {coder: file:c.yml, n: 1}\n")
    theatre.write("c.yml", "model: x\n")
    theatre.set("agents.coder.t", "1")
    assert theatre.read() == "agents: {coder: {$ref: file:c.yml, t: 1}, n: 1}\n"
    assert theatre.get("agents.coder.model") == "x\n"


def test_nested_include_writes_in_its_including_file(theatre):
    theatre.write("troupe.yml", "team: file:team.yml\n")
    theatre.write("team.yml", "coder: file:c.yml\n")
    theatre.write("c.yml", "model: x\n")
    theatre.set("team.coder.t", "1")
    assert theatre.read("c.yml") == "model: x\n"
    assert theatre.read("team.yml") == "coder:\n  $ref: file:c.yml\n  t: 1\n"
    assert theatre.read() == "team: file:team.yml\n"


def test_whole_file_include_document(theatre):
    theatre.write("troupe.yml", "a: file:a.yml\n")
    theatre.write("a.yml", "file:b.yml\n")
    theatre.write("b.yml", "x: 1\n")
    theatre.set("a.y", "2")
    assert theatre.read("b.yml") == "x: 1\n"
    assert theatre.read("a.yml") == "$ref: file:b.yml\ny: 2\n"
    assert theatre.get("a.x") == "1\n"


def test_overrides_only_for_yaml_includes(theatre):
    theatre.write("troupe.yml", "p:\n  $ref: file:p.md\n")
    theatre.write("p.md", "hello\n")
    assert theatre.get("p") == "hello\n"
    theatre.write("troupe.yml", "p:\n  $ref: file:p.md\n  x: 1\n")
    assert theatre.run("get", "p")[0] == 4
    theatre.write("troupe.yml", "p:\n  $ref: 5\n")
    assert theatre.run("get", "p")[0] == 4


def test_replacing_a_merged_section_writes_each_key_where_it_lives(theatre):
    theatre.write("troupe.yml", "coder:\n  $ref: file:c.yml\n  params:\n    a: 10\n")
    theatre.write("c.yml", "params:\n  a: 1\n  b: 2\n  c: 3\n")
    theatre.set("coder.params", "{a: 11, b: 2, d: 4}")
    assert theatre.read("c.yml") == "params:\n  a: 1\n  b: 2\n"
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  params:\n    a: 11\n    d: 4\n"
    theatre.set("coder", "{params: {a: 1}}", expect=3)


# ---------------------------------------------------------------- --in-include


def test_in_include_writes_new_key_into_included_file(theatre):
    theatre.write("troupe.yml", "agents:\n  coder: file:agents/coder.yaml  # c\n")
    theatre.write("agents/coder.yaml", "model: x\nparams:\n  a: 1\n")
    theatre.set("--in-include", "agents.coder.temperature", "0.3")
    theatre.set("agents.coder.params.b", "2", "--in-include")
    assert theatre.read() == "agents:\n  coder: file:agents/coder.yaml  # c\n"
    assert theatre.read("agents/coder.yaml") == "model: x\nparams:\n  a: 1\n  b: 2\ntemperature: 0.3\n"


def test_in_include_through_mapping_form(theatre):
    theatre.write("troupe.yml", "coder:\n  $ref: file:c.yml\n  model: y\n  params:\n    a: 10\n")
    theatre.write("c.yml", "model: x\nparams:\n  a: 1\n")
    theatre.set("--in-include", "coder.t", "1")
    theatre.set("--in-include", "coder.params.b", "2")
    theatre.set("--in-include", "coder.model", "z")  # existing: still changes where it is set
    assert theatre.read() == "coder:\n  $ref: file:c.yml\n  model: z\n  params:\n    a: 10\n"
    assert theatre.read("c.yml") == "model: x\nparams:\n  a: 1\n  b: 2\nt: 1\n"


def test_in_include_without_include_is_ordinary_set(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.set("--in-include", "b", "2")
    assert theatre.read() == "a: 1\nb: 2\n"

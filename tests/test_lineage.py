import json
import re
import sqlite3
import subprocess
import sys

import pytest

AT = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


@pytest.fixture(autouse=True)
def _no_actor(monkeypatch):
    monkeypatch.delenv("TROUPE_ACTOR", raising=False)


def lineage(e):
    return e["actor"], e["via"], e["reason"]


def test_import_is_unknown_actor_one_change(theatre):
    theatre.write("troupe.yml", "a: 1\nb: 2\n")
    h = theatre.history()
    assert [lineage(e) for e in h] == [({"kind": "unknown", "id": None}, "import", None)] * 2
    assert h[0]["change"] == h[1]["change"]
    assert all(AT.match(e["at"]) for e in h)


def test_set_records_actor_reason_and_one_change(theatre, monkeypatch):
    theatre.write("troupe.yml", "m: {x: 1, y: 2}\n")
    monkeypatch.setenv("TROUPE_ACTOR", "agent:claude-1")
    theatre.set("m", "{x: 3, z: 4}", "--reason", "tune m")
    h = theatre.history()[2:]
    assert [(e["op"], e["path"]) for e in h] == [("set", "m.x"), ("set", "m.z"), ("remove", "m.y")]
    assert {(str(lineage(e)), e["change"], e["at"]) for e in h} == {
        (str(({"kind": "agent", "id": "claude-1"}, "cli", "tune m")), h[0]["change"], h[0]["at"])
    }
    assert AT.match(h[0]["at"])
    assert h[0]["change"] != theatre.history()[0]["change"]


def test_each_command_gets_its_own_change(theatre, monkeypatch):
    theatre.write("troupe.yml", "a: 1\n")
    monkeypatch.setenv("TROUPE_ACTOR", "human:ada")
    theatre.set("a", "2")
    theatre.set("a", "3")
    h = theatre.history()
    assert len({e["change"] for e in h}) == 3
    assert [lineage(e) for e in h[1:]] == [({"kind": "human", "id": "ada"}, "cli", None)] * 2


def test_default_actor_is_login_name(theatre, monkeypatch):
    import getpass

    theatre.write("troupe.yml", "a: 1\n")
    theatre.set("a", "2")
    assert theatre.history()[-1]["actor"] == {"kind": "human", "id": getpass.getuser()}


def test_hand_edits_are_file_events_apart_from_the_set(theatre, monkeypatch):
    theatre.write("troupe.yml", "a: 1\nb: 1\n")
    theatre.get("a")
    theatre.write("troupe.yml", "a: 2\nb: 1\n")
    monkeypatch.setenv("TROUPE_ACTOR", "agent:x")
    theatre.set("b", "3", "--reason", "why")
    edit, setb = theatre.history()[2:]
    assert lineage(edit) == ({"kind": "unknown", "id": None}, "file", None)
    assert lineage(setb) == ({"kind": "agent", "id": "x"}, "cli", "why")
    assert edit["change"] != setb["change"]


@pytest.mark.parametrize("spec", ["", "bob", "robot:x", "human:", "agent:", ":x"])
def test_bad_actor_is_usage_error(theatre, monkeypatch, spec):
    theatre.write("troupe.yml", "a: 1\n")
    monkeypatch.setenv("TROUPE_ACTOR", spec)
    for args in (("get", "a"), ("set", "a", "2"), ("history",)):
        assert theatre.run(*args)[0] == 2
    assert theatre.read() == "a: 1\n"
    monkeypatch.delenv("TROUPE_ACTOR")
    assert len(theatre.history()) == 1


def test_lineage_survives_restart(theatre, tmp_path):
    theatre.write("troupe.yml", "a: 1\n")
    env = {"TROUPE_ACTOR": "agent:bot", "PATH": ""}
    run = [sys.executable, "-m", "troupe.cli", "config"]
    subprocess.run([*run, "set", "a", "2", "--reason", "r", "--theatre", str(tmp_path)], env=env, check=True)
    r = subprocess.run([*run, "history", "--theatre", str(tmp_path)], env=env, capture_output=True, text=True)
    assert '"actor": {"kind": "agent", "id": "bot"}, "via": "cli"' in r.stdout.splitlines()[1]
    assert '"reason": "r"' in r.stdout


def test_events_from_before_lineage_say_what_is_unknown(theatre):
    """A state file written before lineage existed: the import is known, a later set's origin is not."""
    theatre.write("troupe.yml", "a: 1\n")
    theatre.history()
    db = sqlite3.connect(theatre.dir / "troupe-state.sqlite")
    rows = db.execute("SELECT originator_version, topic, state FROM stored_events").fetchall()
    for version, topic, state in rows:
        if "Leaf" in topic:
            data = json.loads(state)
            del data["lineage"], data["class_version"]
            db.execute(
                "UPDATE stored_events SET state = ? WHERE originator_version = ?",
                (json.dumps(data).encode(), version),
            )
    db.commit()
    db.close()
    (imported,) = theatre.history()
    assert lineage(imported) == ({"kind": "unknown", "id": None}, "import", None)
    assert imported["change"] == "import"
    assert AT.match(imported["at"])

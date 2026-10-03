import os
import signal
import subprocess
import sys
import time

import pytest

# ---------------------------------------------------------------- reconcile on every command


def ops(history):
    return [(e["op"], e["path"], e["old"], e["new"]) for e in history]


def test_commands_reconcile_hand_edits(theatre):
    theatre.write("troupe.yml", "a: 1\nb: x\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: 2\nc: y\n")
    # history reports what has been recorded: the edit is not picked up yet
    assert ops(theatre.history()) == [("import", "a", None, 1), ("import", "b", None, "x")]
    assert theatre.show("a") == "2\n"
    assert ops(theatre.history())[2:] == [("set", "a", 1, 2), ("set", "c", None, "y"), ("remove", "b", "x", None)]
    assert theatre.show("a") == "2\n"
    assert len(theatre.history()) == 5


def test_set_reconciles_first(theatre):
    theatre.write("troupe.yml", "a: 1\nb: 1\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: 2\nb: 1\n")
    theatre.set("b", "3")
    assert ops(theatre.history())[2:] == [("set", "a", 1, 2), ("set", "b", 1, 3)]


def test_reconcile_failure_shows_error(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: [1\n")
    assert theatre.run("show", "a")[0] == 4
    assert len(theatre.history()) == 1  # history needs no loadable files once recorded


def test_formatting_and_comment_edits_emit_nothing(theatre):
    theatre.write("troupe.yml", "a: 1\nm: {x: [1, 2]}\ns: hi\n")
    theatre.show("a")
    theatre.write("troupe.yml", "# comment\na:   0x1  # one\nm:\n  x:\n    - 1\n    - 2\ns: 'hi'\n")
    theatre.show("a")
    assert [e["op"] for e in theatre.history()] == ["import"] * 4


def test_type_change_is_a_change(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: '1'\n")
    theatre.show("a")
    assert ops(theatre.history())[1:] == [("set", "a", 1, "1")]


def test_special_values_round_trip(theatre):
    theatre.write("troupe.yml", "a: .nan\nb: -.inf\nc: null\nd: \"x\\r\\ny\"\ne: 123456789012345678901234567890\n")
    theatre.show("a")
    theatre.show("a")
    assert len(theatre.history()) == 5


def test_hand_added_include_is_followed(theatre):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.show("a")
    theatre.write("inc.yml", "x: 1\ny: [2]\n")
    theatre.write("troupe.yml", "a: 1\nb: file:inc.yml\n")
    theatre.show("a")
    assert ops(theatre.history())[1:] == [("set", "b.x", None, 1), ("set", "b.y.0", None, 2)]


# ---------------------------------------------------------------- watch


class Watch:
    def __init__(self, theatre):
        self.theatre = theatre
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "troupe.cli", "config", "watch", "--theatre", str(theatre.root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.line = self.proc.stdout.readline()

    def stop(self, sig=signal.SIGTERM) -> int:
        self.proc.send_signal(sig)
        code = self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return code


@pytest.fixture
def watch(theatre):
    watches = []

    def start():
        w = Watch(theatre)
        watches.append(w)
        return w

    yield start
    for w in watches:
        if w.proc.poll() is None:
            w.stop(signal.SIGKILL)


def wait_history(theatre, n, timeout=2.0):
    deadline = time.monotonic() + timeout
    while True:
        h = theatre.history()
        if len(h) >= n or time.monotonic() > deadline:
            return h
        time.sleep(0.05)


def test_watch_prints_line_and_exits_0(theatre, watch):
    theatre.write("troupe.yml", "a: 1\n")
    w = watch()
    assert w.line == f"watching {theatre.root}\n"
    assert w.stop(signal.SIGINT) == 0
    w = watch()
    assert w.stop(signal.SIGTERM) == 0


def test_watch_fails_on_broken_config(theatre, watch):
    theatre.write("troupe.yml", "a: [1\n")
    w = watch()
    assert w.line == ""
    assert w.proc.wait(timeout=10) == 4


def test_watch_picks_up_edits(theatre, watch):
    theatre.write("troupe.yml", "a: 1\n")
    theatre.write("t.txt", "one\n")
    theatre.show("a")
    theatre.write("troupe.yml", "a: 2\n")  # edited while nothing ran
    w = watch()
    assert ops(theatre.history())[1:] == [("set", "a", 1, 2)]
    theatre.write("troupe.yml", "a: 3\nt: file:t.txt\n")
    assert ops(wait_history(theatre, 4))[2:] == [("set", "a", 2, 3), ("set", "t", None, "one\n")]
    theatre.write("t.txt", "two\n")
    assert ops(wait_history(theatre, 5))[4:] == [("set", "t", "one\n", "two\n")]
    theatre.write("troupe.yml", "# just a comment\na:   3\nt: 'file:t.txt'\n")
    time.sleep(0.6)
    assert len(theatre.history()) == 5
    assert w.stop() == 0


def test_watch_survives_broken_edit(theatre, watch):
    theatre.write("troupe.yml", "a: 1\n")
    w = watch()
    theatre.write("troupe.yml", "a: [1\n")
    time.sleep(0.4)
    theatre.write("troupe.yml", "a: 2\n")
    assert ops(wait_history(theatre, 2)) == [("import", "a", None, 1), ("set", "a", 1, 2)]
    assert w.stop() == 0


def test_watch_follows_new_include(theatre, watch):
    theatre.write("troupe.yml", "a: 1\n")
    w = watch()
    theatre.write("troupe.yml", "a: 1\nb: file:inc/b.yml\n")  # target not there yet
    time.sleep(0.3)
    theatre.write("inc/b.yml", "x: 1\n")
    assert ops(wait_history(theatre, 2))[1:] == [("set", "b.x", None, 1)]
    theatre.write("inc/b.yml", "x: 2\n")
    assert ops(wait_history(theatre, 3))[2:] == [("set", "b.x", 1, 2)]
    assert w.stop() == 0


def test_set_while_watching_emits_only_its_own_events(theatre, watch):
    theatre.write("troupe.yml", "a: 1\nm:\n  x: 1\n  y: 2\n")
    w = watch()
    for i in range(2, 12):
        theatre.set("a", str(i))
    theatre.set("m", "{x: 5}")
    time.sleep(0.6)
    h = ops(theatre.history())
    assert h[3:] == [("set", "a", i - 1, i) for i in range(2, 12)] + [
        ("set", "m.x", 1, 5),
        ("remove", "m.y", 2, None),
    ]
    assert w.stop() == 0


def test_set_in_subprocess_while_watching(theatre, watch):
    theatre.write("troupe.yml", "a: 1\n")
    w = watch()
    env = dict(os.environ, TROUPE_THEATRE=str(theatre.root))
    for i in range(2, 7):
        subprocess.run([sys.executable, "-m", "troupe.cli", "config", "set", "a", str(i)], env=env, check=True)
    time.sleep(0.6)
    assert ops(theatre.history())[1:] == [("set", "a", i - 1, i) for i in range(2, 7)]
    assert w.stop() == 0

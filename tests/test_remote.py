import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class Server:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.requests: list[str] = []
        files, requests = self.files, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                body = files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()
    s.httpd.server_close()


def test_remote_yaml_and_text_includes(theatre, server):
    server.files["/team/coder.yml"] = "model: opus\nparams:\n  t: 1\n"
    server.files["/team/prompt"] = "Be brief.\n"
    theatre.write("troupe.yml", f"coder: {server.url}/team/coder.yml\nprompt: {server.url}/team/prompt\n")
    assert theatre.get("coder.model") == "opus\n"
    assert theatre.get("coder.params.t") == "1\n"
    assert theatre.get("prompt") == "Be brief.\n"
    code, out, _ = theatre.run("show", "--sources")
    assert code == 0
    assert f"  model: opus # from {server.url}/team/coder.yml\n" in out
    assert f"prompt: |\n" in out or f"prompt: | # from {server.url}/team/prompt\n" in out
    assert {e["path"] for e in theatre.history()} == {"coder.model", "coder.params.t", "prompt"}


def test_failed_fetch_exits_4(theatre, server):
    theatre.write("troupe.yml", f"coder: {server.url}/missing.yml\n")
    code, _, err = theatre.run("get", "coder")
    assert code == 4 and "/missing.yml" in err
    theatre.write("troupe.yml", "coder: http://127.0.0.1:1/x.yml\n")
    assert theatre.run("get", "coder")[0] == 4


def test_changing_a_remote_value_is_refused_naming_the_include(theatre, server):
    server.files["/c.yml"] = "model: opus\nprompt: file:p.txt\n"
    server.files["/p.txt"] = "hi"
    url = f"{server.url}/c.yml"
    text = f"agents:\n  coder: '{url}'  # team\n"
    theatre.write("troupe.yml", text)
    for path, value, extra in [
        ("agents.coder.model", "sonnet", ()),
        ("agents.coder.model", "haiku", ("--expand-alias",)),
        ("agents.coder", "{model: x}", ()),
        ("agents.coder.prompt", "bye", ()),  # a text include relative to the remote file
        ("agents.coder.new", "1", ("--in-include",)),
    ]:
        code, _, err = theatre.run("set", path, value, *extra)
        assert code == 3, (path, err)
        assert f"'{url}'" in err, err
        assert "troupe.yml:2" in err
    assert theatre.read() == text
    assert theatre.get("agents.coder.prompt") == "hi\n"
    theatre.set("agents.coder.model", "opus")  # unchanged: nothing to write


def test_refusal_names_the_local_line_of_a_nested_remote(theatre, server):
    server.files["/a.yml"] = f"inner: {server.url}/b.yml\n"
    server.files["/b.yml"] = "x: 1\n"
    theatre.write("troupe.yml", "top: file:local.yml\n")
    theatre.write("local.yml", f"shared: {server.url}/a.yml\n")
    code, _, err = theatre.run("set", "top.shared.inner.x", "2")
    assert code == 3
    assert f"{server.url}/b.yml" in err and f"local.yml:1: {server.url}/a.yml" in err


def test_new_key_under_remote_include_is_a_local_override(theatre, server):
    server.files["/c.yml"] = "model: opus\n"
    theatre.write("troupe.yml", f"coder: {server.url}/c.yml\n")
    theatre.set("coder.t", "0.3")
    assert theatre.read() == f"coder:\n  $ref: {server.url}/c.yml\n  t: 0.3\n"
    assert theatre.get("coder.model") == "opus\n"
    assert theatre.get("coder.t") == "0.3\n"
    theatre.set("coder.model", "sonnet", expect=3)
    theatre.set("coder.t", "0.5")  # an override is local
    assert theatre.read() == f"coder:\n  $ref: {server.url}/c.yml\n  t: 0.5\n"


def test_set_include_like_string_is_written_literal(theatre, server):
    server.files["/c.yml"] = "model: opus\n"
    theatre.write("troupe.yml", "a: 1\n")
    theatre.set("a", f"{server.url}/c.yml")
    theatre.set("b", "file:nothing.yml")
    theatre.set("c", "[file:x, y]")
    assert theatre.read() == (
        f"a: !literal {server.url}/c.yml\nb: !literal file:nothing.yml\nc:\n  - !literal file:x\n  - y\n"
    )
    assert theatre.get("a") == f"{server.url}/c.yml\n"
    assert theatre.get("b") == "file:nothing.yml\n"
    assert server.requests == []
    code, out, _ = theatre.run("show")
    assert out == f"a: !literal {server.url}/c.yml\nb: !literal file:nothing.yml\nc:\n  - !literal file:x\n  - y\n"
    # With --include, the string is an include; a !literal tag on the old value goes.
    theatre.set("a", f"{server.url}/c.yml", "--include")
    assert theatre.read().startswith(f"a: {server.url}/c.yml\n")
    assert theatre.get("a.model") == "opus\n"
    theatre.write("p.md", "text\n")
    theatre.set("d", "file:p.md", "--include")
    assert theatre.get("d") == "text\n"
    # An include that does not exist is not written.
    theatre.set("e", "file:missing.yml", "--include", expect=3)
    code, _, _ = theatre.run("set", "e", f"{server.url}/missing.yml", "--include")
    assert code == 4
    assert "\ne:" not in theatre.read()


def test_literal_tag_is_read_as_literal(theatre):
    theatre.write("troupe.yml", "a: !literal file:none.yml\nb: !literal https://example.invalid/x.yml\n")
    assert theatre.get("a") == "file:none.yml\n"
    assert theatre.get("b") == "https://example.invalid/x.yml\n"
    theatre.set("a", "file:other.yml")
    assert theatre.read().startswith("a: !literal file:other.yml\n")


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "endpoint": {"type": "string", "format": "uri"},
        "mirrors": {"type": "array", "items": {"type": "string", "format": "uri"}},
        "hooks": {"type": "object", "additionalProperties": {"type": "string", "format": "uri"}},
        "agents": {"type": "object", "additionalProperties": {"type": "object"}},
        "level": {"type": "integer", "maximum": 5},
    },
}


def test_schema_uri_strings_are_literals(theatre, server):
    theatre.write("troupe.schema.json", json.dumps(SCHEMA))
    theatre.write(
        "troupe.yml",
        "endpoint: https://api.example.invalid/v1.yml\nmirrors: [file:m.yml]\nhooks:\n  done: http://h.invalid/\n",
    )
    assert theatre.get("endpoint") == "https://api.example.invalid/v1.yml\n"
    assert theatre.get("mirrors.0") == "file:m.yml\n"
    assert theatre.get("hooks.done") == "http://h.invalid/\n"
    code, out, _ = theatre.run("show")
    assert out == "endpoint: https://api.example.invalid/v1.yml\nmirrors:\n  - file:m.yml\nhooks:\n  done: http://h.invalid/\n"
    # Setting a URI path writes the string plain; --include cannot make it an include.
    theatre.set("hooks.start", "https://s.invalid/")
    assert theatre.read().endswith("  done: http://h.invalid/\n  start: https://s.invalid/\n")
    theatre.set("endpoint", "file:x.yml", "--include", expect=3)


def test_schema_validation(theatre):
    theatre.write("troupe.schema.json", json.dumps(SCHEMA))
    theatre.write("troupe.yml", "level: 9\n")
    code, _, err = theatre.run("get", "level")
    assert code == 4 and "level" in err
    theatre.write("troupe.yml", "level: 3\n")
    theatre.set("level", "7", expect=3)
    theatre.set("level", "x", expect=3)
    theatre.set("agents", "[1]", expect=3)
    assert theatre.read() == "level: 3\n"
    theatre.set("level", "4")
    assert theatre.read() == "level: 4\n"
    theatre.write("troupe.schema.json", "{not json")
    assert theatre.run("get", "level")[0] == 4
    theatre.write("troupe.schema.json", json.dumps({"type": 5}))
    assert theatre.run("get", "level")[0] == 4


def test_schema_applies_to_included_values(theatre, server):
    theatre.write("troupe.schema.json", json.dumps(SCHEMA))
    server.files["/agents.yml"] = "coder:\n  model: 1\n"
    theatre.write("troupe.yml", f"agents: {server.url}/agents.yml\nlevel: 1\n")
    assert theatre.get("agents.coder.model") == "1\n"
    server.files["/agents.yml"] = "coder: 1\n"  # agents.coder must be an object
    assert theatre.run("get", "level")[0] == 4


def test_mapping_form_remote_include(theatre, server):
    server.files["/c.yml"] = "model: opus\ntemp: 1\n"
    url = f"{server.url}/c.yml"
    theatre.write("troupe.yml", f"coder:\n  $ref: \"{url}\"\n  model: sonnet\n")
    assert theatre.get("coder.model") == "sonnet\n"
    assert theatre.get("coder.temp") == "1\n"
    theatre.set("coder.model", "haiku")  # the override is local
    code, _, err = theatre.run("set", "coder.temp", "2")
    assert code == 3 and f'"{url}"' in err and "troupe.yml:2" in err
    theatre.set("coder.new", "x")
    assert theatre.read() == f"coder:\n  $ref: \"{url}\"\n  model: haiku\n  new: x\n"


def test_watch_ignores_remote_includes(theatre, server):
    import signal
    import subprocess
    import sys
    import time

    server.files["/c.yml"] = "model: opus\n"
    theatre.write("troupe.yml", f"coder: {server.url}/c.yml\nlevel: 1\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "troupe.cli", "config", "watch", "--theatre", str(theatre.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert proc.stdout.readline().startswith(b"watching")
        theatre.write("troupe.yml", f"coder: {server.url}/c.yml\nlevel: 2\n")
        for _ in range(50):
            if any(e["path"] == "level" and e["op"] == "set" for e in theatre.history()):
                break
            time.sleep(0.1)
        else:
            pytest.fail("watch did not reconcile the edit")
    finally:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0

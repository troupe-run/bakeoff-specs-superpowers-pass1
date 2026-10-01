import json
from pathlib import Path

import pytest

from troupe.cli import main


class Theatre:
    def __init__(self, root: Path, capsys):
        self.root = root
        self.dir = root / ".troupe"
        self.dir.mkdir()
        self.capsys = capsys

    def write(self, rel: str, text: str) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        return p

    def read(self, rel: str = "troupe.yml") -> str:
        return (self.dir / rel).read_bytes().decode("utf-8")

    def run(self, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = main(["config", args[0], "--theatre", str(self.root), *args[1:]])
        out, err = self.capsys.readouterr()
        if code:
            assert out == ""
            assert err
        return code, out, err

    def show(self, path: str) -> str:
        code, out, err = self.run("show", path)
        assert code == 0, err
        return out

    def set(self, path: str, value: str, expect: int = 0) -> None:
        code, _, err = self.run("set", path, value)
        assert code == expect, err

    def history(self, path: str | None = None) -> list[dict]:
        code, out, err = self.run("history", *([path] if path else []))
        assert code == 0, err
        return [json.loads(line) for line in out.splitlines()]


@pytest.fixture
def theatre(tmp_path, capsys):
    return Theatre(tmp_path, capsys)

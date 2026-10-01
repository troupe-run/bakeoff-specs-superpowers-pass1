"""The configuration as an event-sourced projection, persisted in SQLite under `.troupe/`."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from eventsourcing.application import AggregateNotFoundError, Application
from eventsourcing.domain import Aggregate, event

STATE_FILE = "troupe-state.sqlite"


class TheatreConfig(Aggregate):
    """Projection of a theatre's configuration: current leaf values and the log of leaf changes."""

    @staticmethod
    def create_id() -> UUID:
        return uuid5(NAMESPACE_URL, "troupe:theatre-config")

    @event("Created")
    def __init__(self) -> None:
        self.leaves: dict[str, Any] = {}
        self.log: list[dict[str, Any]] = []

    @event("LeafImported")
    def leaf_imported(self, path: str, new: Any) -> None:
        self._record("import", path, None, new)

    @event("LeafSet")
    def leaf_set(self, path: str, old: Any, new: Any) -> None:
        self._record("set", path, old, new)

    @event("LeafRemoved")
    def leaf_removed(self, path: str, old: Any) -> None:
        self._record("remove", path, old, None)

    def _record(self, op: str, path: str, old: Any, new: Any) -> None:
        if op == "remove":
            self.leaves.pop(path, None)
        else:
            self.leaves[path] = new
        self.log.append({"seq": len(self.log) + 1, "op": op, "path": path, "old": old, "new": new})


class ConfigStore(Application):
    def __init__(self, troupe_dir: Path) -> None:
        super().__init__(
            env={
                "PERSISTENCE_MODULE": "eventsourcing.sqlite",
                "SQLITE_DBNAME": str(troupe_dir / STATE_FILE),
            }
        )

    def _get(self) -> TheatreConfig | None:
        try:
            return self.repository.get(TheatreConfig.create_id())
        except AggregateNotFoundError:
            return None

    def ensure_imported(self, leaves: dict[str, Any]) -> None:
        if self._get() is not None:
            return
        agg = TheatreConfig()
        for path, value in leaves.items():
            agg.leaf_imported(path, value)
        self.save(agg)

    def record(self, changes: list[tuple[str, str, Any, Any]]) -> None:
        if not changes:
            return
        agg = self._get()
        assert agg is not None
        for op, path, old, new in changes:
            if op == "remove":
                agg.leaf_removed(path, old)
            else:
                agg.leaf_set(path, old, new)
        self.save(agg)

    def history(self) -> list[dict[str, Any]]:
        agg = self._get()
        return list(agg.log) if agg else []

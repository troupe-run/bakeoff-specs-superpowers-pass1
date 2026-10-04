"""The configuration as an event-sourced projection, persisted in SQLite under `.troupe/`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from eventsourcing.application import AggregateNotFoundError, Application
from eventsourcing.domain import Aggregate, event

from .yamlcore import same

STATE_FILE = "troupe-state.sqlite"

UNKNOWN_ACTOR = {"kind": "unknown", "id": None}


def rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def lineage(via: str, actor: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    """Who/how/when/why for one change: every event recorded with it shares its fresh `change` id."""
    return {
        "actor": dict(actor or UNKNOWN_ACTOR),
        "via": via,
        "at": rfc3339(datetime.now(UTC)),
        "reason": reason,
        "change": str(uuid4()),
    }


def _legacy_lineage(state: dict[str, Any], via: str | None, change: str | None) -> None:
    """Lineage for an event recorded before lineage existed: only its time and import-ness are known."""
    state["lineage"] = {
        "actor": dict(UNKNOWN_ACTOR),
        "via": via,
        "at": rfc3339(state["timestamp"]),
        "reason": None,
        "change": change,
    }


class TheatreConfig(Aggregate):
    """Projection of a theatre's configuration: current leaf values and the log of leaf changes."""

    @staticmethod
    def create_id() -> UUID:
        return uuid5(NAMESPACE_URL, "troupe:theatre-config")

    @event("Created")
    def __init__(self) -> None:
        self.leaves: dict[str, Any] = {}
        self.log: list[dict[str, Any]] = []

    # Version 2 added `lineage`. Version 1 events came from the one import or from a `set` or reconcile,
    # indistinguishably; the import was a single unit, so it gets the change id "import".
    class LeafImported(Aggregate.Event):
        class_version = 2
        path: str
        new: Any
        lineage: dict[str, Any]

        @staticmethod
        def upcast_v1_v2(state: dict[str, Any]) -> None:
            _legacy_lineage(state, "import", "import")

    class LeafSet(Aggregate.Event):
        class_version = 2
        path: str
        old: Any
        new: Any
        lineage: dict[str, Any]

        @staticmethod
        def upcast_v1_v2(state: dict[str, Any]) -> None:
            _legacy_lineage(state, None, None)

    class LeafRemoved(Aggregate.Event):
        class_version = 2
        path: str
        old: Any
        lineage: dict[str, Any]

        @staticmethod
        def upcast_v1_v2(state: dict[str, Any]) -> None:
            _legacy_lineage(state, None, None)

    @event(LeafImported)
    def leaf_imported(self, path: str, new: Any, lineage: dict[str, Any]) -> None:
        self._record("import", path, None, new, lineage)

    @event(LeafSet)
    def leaf_set(self, path: str, old: Any, new: Any, lineage: dict[str, Any]) -> None:
        self._record("set", path, old, new, lineage)

    @event(LeafRemoved)
    def leaf_removed(self, path: str, old: Any, lineage: dict[str, Any]) -> None:
        self._record("remove", path, old, None, lineage)

    def _record(self, op: str, path: str, old: Any, new: Any, lineage: dict[str, Any]) -> None:
        if op == "remove":
            self.leaves.pop(path, None)
        else:
            self.leaves[path] = new
        entry = {"seq": len(self.log) + 1, "op": op, "path": path, "old": old, "new": new}
        self.log.append(entry | lineage)


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

    def exists(self) -> bool:
        return self._get() is not None

    def ensure_imported(self, leaves: dict[str, Any]) -> None:
        if self._get() is not None:
            return
        agg = TheatreConfig()
        how = lineage("import")
        for path, value in leaves.items():
            agg.leaf_imported(path, value, how)
        self.save(agg)

    def record(self, changes: list[tuple[str, str, Any, Any]], how: dict[str, Any]) -> None:
        """Record `changes` as one change, described by `how` (see `lineage`)."""
        if not changes:
            return
        agg = self._get()
        assert agg is not None
        for op, path, old, new in changes:
            if op == "remove":
                agg.leaf_removed(path, old, how)
            else:
                agg.leaf_set(path, old, new, how)
        self.save(agg)

    def reconcile(self, leaves: dict[str, Any]) -> None:
        """Record a `set`/`remove` for each leaf where `leaves` (the files) differ from the projection."""
        agg = self._get()
        assert agg is not None
        changes = diff(agg.leaves, leaves)
        if changes:
            self.record(changes, lineage("file"))

    def history(self) -> list[dict[str, Any]]:
        agg = self._get()
        return list(agg.log) if agg else []


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, str, Any, Any]]:
    """The leaf changes from `before` to `after`: sets in `after`'s order, then removes."""
    changes: list[tuple[str, str, Any, Any]] = []
    for p, v in after.items():
        if p not in before:
            changes.append(("set", p, None, v))
        elif not same(before[p], v):
            changes.append(("set", p, before[p], v))
    for p, v in before.items():
        if p not in after:
            changes.append(("remove", p, v, None))
    return changes

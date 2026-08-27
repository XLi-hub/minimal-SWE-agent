"""Atomic persistence helpers shared by agent and benchmark workflows.

Keeping trajectory serialization outside the agent loop lets other runners
persist compatible artifacts without depending on orchestration internals.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace *path* with UTF-8 *text*."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def trajectory_events_path(path: Path) -> Path:
    """Return the sidecar event-log path for a trajectory path."""

    suffix = ".traj.json"
    if path.name.endswith(suffix):
        stem = path.name[:-len(suffix)]
    else:
        stem = path.stem
    return path.with_name(f"{stem}.events.jsonl")


def save_trajectory_data(path: Path, data: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a compact trajectory and its append-only raw event sidecar.

    The returned mapping is exactly what is written to the main trajectory.
    In-memory serializers may include ``events`` inline; persistence moves
    them to JSONL and leaves a relative, count-checked reference.
    """

    persisted = copy.deepcopy(dict(data))
    events = persisted.pop("events", None)
    if events is not None:
        event_path = trajectory_events_path(path)
        event_text = "".join(
            json.dumps(event, ensure_ascii=False, default=repr) + "\n"
            for event in events
        )
        atomic_write_text(event_path, event_text)
        persisted["event_log"] = {
            "path": event_path.name,
            "format": "mini-agent-events-0.1",
            "event_count": len(events),
        }
    atomic_write_text(
        path,
        json.dumps(persisted, indent=2, ensure_ascii=False, default=repr) + "\n",
    )
    return persisted


__all__ = [
    "atomic_write_text",
    "save_trajectory_data",
    "trajectory_events_path",
]

import json

from mini_agent.persistence import (
    atomic_write_text,
    save_trajectory_data,
    trajectory_events_path,
)


def test_atomic_write_text_creates_parents_and_replaces_content(tmp_path):
    path = tmp_path / "nested" / "result.txt"

    atomic_write_text(path, "first")
    atomic_write_text(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_trajectory_events_path_handles_conventional_and_generic_names(tmp_path):
    assert trajectory_events_path(tmp_path / "run.traj.json").name == "run.events.jsonl"
    assert trajectory_events_path(tmp_path / "run.json").name == "run.events.jsonl"


def test_save_trajectory_data_extracts_events_without_mutating_input(tmp_path):
    path = tmp_path / "run.traj.json"
    data = {
        "messages": [{"role": "user", "content": "task"}],
        "events": [{"sequence": 0, "type": "message"}],
    }

    persisted = save_trajectory_data(path, data)

    assert "events" in data
    assert "events" not in persisted
    assert persisted["event_log"] == {
        "path": "run.events.jsonl",
        "format": "mini-agent-events-0.1",
        "event_count": 1,
    }
    assert json.loads(path.read_text(encoding="utf-8")) == persisted
    assert json.loads((tmp_path / "run.events.jsonl").read_text(encoding="utf-8")) == data["events"][0]

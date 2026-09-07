"""Tests for trajectory search and structural filtering."""

import json

import pytest

from mini_agent.config import build_config
from mini_agent.tools import execute_tool_call

from ._helpers import FakeEnv, _tc


def test_trajectory_search_returns_prior_evidence_with_continuation():
    cfg = build_config(
        cli_overrides={"tools": {"enabled": ["bash", "submit", "trajectory"]}}
    )
    events = [
        {"sequence": 0, "type": "message", "message": {"content": "task"}},
        {"sequence": 1, "type": "message", "message": {"content": "g++ oracle one"}},
        {"sequence": 2, "type": "message", "message": {"content": "unrelated"}},
        {"sequence": 3, "type": "message", "message": {"content": "g++ oracle two"}},
    ]
    messages: list = []

    execute_tool_call(
        _tc("history", "trajectory", {"query": "G++", "events": 1}),
        messages,
        FakeEnv(),
        cfg,
        event_log=events,
    )

    content = messages[0]["content"]
    assert "event 1" in content
    assert "g++ oracle one" in content
    assert "unrelated" not in content
    assert "start=2" in content


def test_trajectory_output_uses_configured_character_budget():
    cfg = build_config(
        cli_overrides={
            "tools": {
                "enabled": ["trajectory"],
                "default_max_chars": 128,
            },
        }
    )
    messages: list = []

    execute_tool_call(
        _tc("history", "trajectory", {}),
        messages,
        FakeEnv(),
        cfg,
        event_log=[{"sequence": 0, "type": "message", "content": "x" * 10_000}],
    )

    assert len(messages[0]["content"]) <= 128
    assert "truncated" in messages[0]["content"]


def test_trajectory_structural_filters_correlate_tool_results():
    cfg = build_config(cli_overrides={"tools": {"enabled": ["trajectory"]}})
    events = [
        {
            "sequence": 0,
            "type": "message",
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "check",
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps({"command": "pytest -q"}),
                        },
                    }
                ],
            },
        },
        {
            "sequence": 1,
            "type": "message",
            "message": {
                "role": "tool",
                "tool_call_id": "check",
                "content": json.dumps({"output": "failed", "returncode": 1}),
            },
        },
        {
            "sequence": 2,
            "type": "context_compression",
            "messages_before": 12,
            "messages_after": 4,
        },
    ]
    messages: list = []

    execute_tool_call(
        _tc(
            "history",
            "trajectory",
            {"role": "tool", "tool_name": "BASH", "returncode": 1},
        ),
        messages,
        FakeEnv(),
        cfg,
        event_log=events,
    )

    content = messages[0]["content"]
    assert "event 1" in content
    assert '\\"returncode\\": 1' in content
    assert "event 0" not in content
    assert "event 2" not in content

    messages = []
    execute_tool_call(
        _tc("history", "trajectory", {"event_type": "CONTEXT_COMPRESSION"}),
        messages,
        FakeEnv(),
        cfg,
        event_log=events,
    )
    assert "event 2" in messages[0]["content"]
    assert "event 1" not in messages[0]["content"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"start": -1},
        {"start": True},
        {"events": 0},
        {"events": 51},
        {"query": 123},
        {"event_type": ""},
        {"role": 1},
        {"tool_name": []},
        {"returncode": True},
        {"returncode": "1"},
    ],
)
def test_trajectory_rejects_invalid_ranges(arguments):
    cfg = build_config(cli_overrides={"tools": {"enabled": ["trajectory"]}})
    messages: list = []

    execute_tool_call(
        _tc("history", "trajectory", arguments),
        messages,
        FakeEnv(),
        cfg,
        event_log=[],
    )

    assert messages[0]["content"].startswith("Error: 'trajectory'")

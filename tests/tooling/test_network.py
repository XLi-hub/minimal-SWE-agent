"""Tests for network-command detection and offline policy enforcement."""

import json
from unittest.mock import MagicMock

import pytest

from mini_agent.config import build_config
from mini_agent.tools import execute_tool_call, find_network_command, get_enabled_tool_schemas

from ._helpers import FakeEnv, _tc


@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("pip download sphinx==4.5", "pip download"),
        (
            "pip show sphinx 2>/dev/null; pip download sphinx==4.5 --no-deps -d /tmp/sphinx 2>&1 | tail -5",
            "pip download",
        ),
        ("cd /testbed && python -m pip install requests", "python -m pip install"),
        ("git -C /testbed fetch origin", "git fetch"),
        ('bash -c "wget https://example.invalid/file"', "wget"),
        ("timeout 30 curl -fsSL https://example.invalid", "curl"),
        ("sudo env FOO=bar apt-get update", "apt-get update"),
    ],
)
def test_find_network_command_detects_common_attempts(command, blocked):
    assert find_network_command(command) == blocked


@pytest.mark.parametrize(
    "command",
    [
        "rg -n 'pip install|git clone' docs/",
        "python -m pip install --no-index --no-deps -e .",
        "git status --short",
        "pytest -q | tail -20",
    ],
)
def test_find_network_command_allows_local_work(command):
    assert find_network_command(command) is None


def test_offline_policy_rejects_network_command_before_environment_execution():
    cfg = build_config(["swebench"])
    env = FakeEnv()
    env.execute = MagicMock(wraps=env.execute)
    messages: list = []

    execute_tool_call(
        _tc("network", "bash", {"command": "pip download sphinx==4.5"}),
        messages,
        env,
        config=cfg,
    )

    env.execute.assert_not_called()
    assert "blocked by policy" in messages[0]["content"]
    assert "pip download" in messages[0]["content"]


def test_offline_bash_schema_does_not_recommend_network_commands():
    cfg = build_config(["swebench"])
    schema = next(
        schema
        for schema in get_enabled_tool_schemas(cfg)
        if schema["function"]["name"] == "bash"
    )
    rendered = json.dumps(schema).lower()

    assert "pip install" not in rendered
    assert "git clone" not in rendered
    assert "external downloads" in rendered

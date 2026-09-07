"""Tests for dispatching tool calls to handlers."""

from mini_agent.config import get_default_config, build_config
from mini_agent.tools import execute_tool_call

from ._helpers import FakeEnv, _tc


def test_unknown_tool_lists_all_available_tools():
    cfg = get_default_config()
    env = FakeEnv()
    messages: list = []
    execute_tool_call(
        _tc("c1", "frobnicate", {}), messages, env, config=cfg
    )
    content = messages[0]["content"]
    for name in ("bash", "submit", "read", "edit", "write"):
        assert name in content


def test_registered_but_disabled_tool_is_not_executed():
    cfg = build_config(["default_bash"])
    env = FakeEnv(files={"secret.txt": "nope"})
    messages: list = []

    execute_tool_call(
        _tc("c1", "read", {"path": "secret.txt"}),
        messages,
        env,
        config=cfg,
    )

    assert env.read_calls == []
    assert "disabled" in messages[0]["content"]

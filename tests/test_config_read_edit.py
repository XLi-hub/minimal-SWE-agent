"""Tests for the two builtin tool configs — 5-tool default + 2-tool legacy."""

from mini_agent.config import build_config, get_default_config
from mini_agent.tools import TOOL_REGISTRY, get_enabled_tool_schemas


def test_default_enables_file_tools():
    for name in ("read", "edit", "write"):
        assert TOOL_REGISTRY[name].schema["function"]["name"] == name


def test_default_enabled_tools_order():
    cfg = get_default_config()
    assert cfg.tools.tool_names() == ["bash", "submit", "read", "edit", "write"]
    assert len(get_enabled_tool_schemas(cfg)) == 5


def test_default_prompt_mentions_file_tools():
    cfg = get_default_config()
    prompt = cfg.agent.system_prompt.lower()
    for name in ("read", "edit", "write"):
        assert name in prompt


def test_legacy_bash_config_has_no_file_tools():
    cfg = build_config(["default_bash"])
    assert cfg.tools.tool_names() == ["bash", "submit"]


def test_legacy_bash_config_inherits_bash_and_submit():
    cfg = build_config(["default_bash"])
    names = [schema["function"]["name"] for schema in get_enabled_tool_schemas(cfg)]
    assert names == ["bash", "submit"]


def test_legacy_bash_config_prompt_mentions_two_tools():
    cfg = build_config(["default_bash"])
    prompt = cfg.agent.system_prompt.lower()
    assert "two tools" in prompt

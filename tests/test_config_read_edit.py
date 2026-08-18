"""Tests for the two builtin tool configs — 5-tool default + 2-tool legacy."""

from src.mini_agent.config import build_config, get_default_config


def test_default_enables_file_tools():
    cfg = get_default_config()
    assert cfg.tools.read_tool["function"]["name"] == "read"
    assert cfg.tools.edit_tool["function"]["name"] == "edit"
    assert cfg.tools.write_tool["function"]["name"] == "write"


def test_default_enabled_tools_order():
    cfg = get_default_config()
    assert cfg.tools.tool_names() == ["bash", "submit", "read", "edit", "write"]
    assert len(cfg.tools.enabled_tools()) == 5


def test_default_prompt_mentions_file_tools():
    cfg = get_default_config()
    prompt = cfg.agent.system_prompt.lower()
    for name in ("read", "edit", "write"):
        assert name in prompt


def test_legacy_bash_config_has_no_file_tools():
    cfg = build_config(["default_bash"])
    assert cfg.tools.read_tool is None
    assert cfg.tools.edit_tool is None
    assert cfg.tools.write_tool is None
    assert cfg.tools.tool_names() == ["bash", "submit"]


def test_legacy_bash_config_inherits_bash_and_submit():
    cfg = build_config(["default_bash"])
    assert cfg.tools.bash_tool["function"]["name"] == "bash"
    assert cfg.tools.submit_tool["function"]["name"] == "submit"


def test_legacy_bash_config_prompt_mentions_two_tools():
    cfg = build_config(["default_bash"])
    prompt = cfg.agent.system_prompt.lower()
    assert "two tools" in prompt

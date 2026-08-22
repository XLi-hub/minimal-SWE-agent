"""Tests for the config pipeline — merge, key=value parsing, env overrides,
template rendering, and validation."""

import pytest
from jinja2 import UndefinedError
from pydantic import ValidationError

from mini_agent.config import (
    UNSET,
    build_config,
    get_default_config,
    recursive_merge,
    render_template,
    _env_var_overrides,
    _key_value_spec_to_nested_dict,
)


# ---------------------------------------------------------------------------
# recursive_merge
# ---------------------------------------------------------------------------

def test_recursive_merge_nested():
    assert recursive_merge(
        {"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}
    ) == {"a": {"b": 1, "c": 3}}


def test_recursive_merge_later_wins():
    assert recursive_merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_recursive_merge_scalar_overrides_dict():
    assert recursive_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}


def test_recursive_merge_skips_unset():
    assert recursive_merge({"a": {"b": 1}}, {"a": {"b": UNSET}}) == {"a": {"b": 1}}


def test_recursive_merge_skips_none_dicts():
    assert recursive_merge({"a": 1}, None, {"b": 2}) == {"a": 1, "b": 2}


def test_recursive_merge_empty():
    assert recursive_merge() == {}


# ---------------------------------------------------------------------------
# key=value spec parsing
# ---------------------------------------------------------------------------

def test_key_value_spec_nested():
    assert _key_value_spec_to_nested_dict("agent.max_steps=500") == {
        "agent": {"max_steps": 500}
    }


def test_key_value_spec_json_decodes_types():
    assert _key_value_spec_to_nested_dict("agent.max_time=1.5") == {
        "agent": {"max_time": 1.5}
    }
    assert _key_value_spec_to_nested_dict('x.s="hi"') == {"x": {"s": "hi"}}


def test_key_value_spec_empty_key_raises():
    with pytest.raises(ValueError):
        _key_value_spec_to_nested_dict(".max_steps=500")


# ---------------------------------------------------------------------------
# env var overrides
# ---------------------------------------------------------------------------

def test_env_var_overrides_nests_double_underscore(monkeypatch):
    monkeypatch.setenv("MINI_AGENT_AGENT__MAX_STEPS", "500")
    assert _env_var_overrides() == {"agent": {"max_steps": 500}}


def test_env_var_overrides_ignores_non_prefixed_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    assert _env_var_overrides() == {}


# ---------------------------------------------------------------------------
# build_config precedence: default < spec < env < cli
# ---------------------------------------------------------------------------

def test_build_config_defaults():
    assert build_config().agent.max_steps == 250


def test_build_config_spec_overrides_default():
    assert build_config(["agent.max_steps=500"]).agent.max_steps == 500


def test_build_config_env_overrides_spec(monkeypatch):
    monkeypatch.setenv("MINI_AGENT_AGENT__MAX_STEPS", "999")
    assert build_config(["agent.max_steps=500"]).agent.max_steps == 999


def test_build_config_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("MINI_AGENT_AGENT__MAX_STEPS", "999")
    config = build_config(
        ["agent.max_steps=500"], {"agent": {"max_steps": 42}}
    )
    assert config.agent.max_steps == 42


def test_build_config_unset_cli_does_not_clobber():
    """CLI 里「未指定」用 UNSET，不覆盖下层已合并的值。"""
    config = build_config(["agent.max_steps=500"], {"agent": {"max_steps": UNSET}})
    assert config.agent.max_steps == 500


# ---------------------------------------------------------------------------
# template rendering
# ---------------------------------------------------------------------------

def test_render_template_substitutes_vars():
    assert render_template("Hello {{ name }}", name="world") == "Hello world"


def test_render_template_raises_on_missing_var():
    with pytest.raises(UndefinedError):
        render_template("{{ missing }}")


# ---------------------------------------------------------------------------
# default config + validation
# ---------------------------------------------------------------------------

def test_get_default_config_is_cached():
    assert get_default_config() is get_default_config()


def test_build_config_rejects_wrong_type():
    with pytest.raises(ValidationError):
        build_config(["agent.max_steps=notanumber"])


def test_build_config_rejects_duplicate_enabled_tools():
    with pytest.raises(ValidationError, match="must be unique"):
        build_config(['tools.enabled=["bash","bash"]'])

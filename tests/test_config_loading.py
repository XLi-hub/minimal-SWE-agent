"""Tests for the config pipeline — merge, key=value parsing, env overrides,
template rendering, and validation."""

import subprocess

import pytest
from jinja2 import UndefinedError
from pydantic import ValidationError

from mini_agent.config import (
    UNSET,
    build_config,
    get_config_path,
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


def test_summary_prompt_preserves_evidence_and_open_gaps():
    prompt = build_config().agent.summary_prompt

    assert "## Invariants / Contracts" in prompt
    assert "## Evidence / Reproductions" in prompt
    assert "## Exact Test Results" in prompt
    assert "## Unverified Assumptions / Coverage Gaps" in prompt
    assert "Separate observations from inferences" in prompt


def test_benchmark_config_is_discoverable_and_valid():
    path = get_config_path("swebench")
    assert path.parent.name == "benchmarks"
    config = build_config(["swebench"])
    assert config.environment.type == "docker"
    assert config.environment.cwd == "/testbed"
    assert config.environment.run_args == ["--rm", "--network=none"]
    assert config.environment.interpreter == ["bash", "-o", "pipefail", "-c"]
    assert config.environment.block_network_commands is True


def test_swebench_interpreter_does_not_mask_pipeline_failures():
    interpreter = build_config(["swebench"]).environment.interpreter

    result = subprocess.run(
        [*interpreter, "false | true"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1


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


def test_build_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        build_config(["environment.not_a_real_option=true"])


def test_environment_config_accepts_docker_execution_options():
    config = build_config([
        'environment.forward_env=["OPENAI_API_KEY"]',
        'environment.executable="podman"',
        'environment.run_args=["--rm","--network=none"]',
        'environment.pull_timeout=9',
        'environment.interpreter=["bash","-c"]',
    ])
    assert config.environment.forward_env == ["OPENAI_API_KEY"]
    assert config.environment.executable == "podman"
    assert config.environment.run_args == ["--rm", "--network=none"]
    assert config.environment.pull_timeout == 9
    assert config.environment.interpreter == ["bash", "-c"]


def test_top_level_run_extension_is_preserved():
    config = build_config(['run.env_startup_command="echo ready"'])
    assert config.run.env_startup_command == "echo ready"


def test_top_level_run_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        build_config(['run.instances=["a"]'])


# ---------------------------------------------------------------------------
# semantic bounds (fail-fast validation)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spec",
    [
        "agent.max_steps=0",
        "agent.max_steps=-1",
        "agent.max_time=0",
        "agent.max_time=-1",
        "agent.cost_limit=-0.01",
        "agent.context_window=0",
        "agent.context_window=-1",
        "agent.compress_threshold=0",
        "agent.compress_threshold=-0.1",
        "agent.compress_threshold=1.1",
        "agent.reserve_tokens=-1",
        "agent.keep_last_n_turns=-1",
        "agent.no_tool_call_retries=-1",
        "tools.default_max_lines=0",
        "tools.default_max_lines=-1",
        "tools.default_max_chars=0",
        "tools.default_max_chars=-1",
        "tools.default_timeout=0",
        "tools.default_timeout=-1",
        "cost.price_input_per_1m=-1",
        "cost.price_input_cache_hit_per_1m=-1",
        "cost.price_output_per_1m=-1",
        "environment.timeout=0",
        "environment.timeout=-1",
        "environment.pull_timeout=0",
        "environment.pull_timeout=-1",
    ],
)
def test_build_config_rejects_non_positive_or_negative_limits(spec):
    with pytest.raises(ValidationError):
        build_config([spec])


@pytest.mark.parametrize(
    "spec",
    [
        'agent.max_time=null',
        'agent.cost_limit=null',
        'agent.cost_limit=0',
        'agent.context_window=2001',
        'agent.compress_threshold=1',
        'agent.reserve_tokens=0',
        'agent.keep_last_n_turns=0',
        'agent.no_tool_call_retries=0',
        'tools.default_max_chars=1',
    ],
)
def test_build_config_accepts_explicit_disabled_or_boundary_values(spec):
    build_config([spec])


@pytest.mark.parametrize("reserve", [64000, 64001])
def test_build_config_requires_reserve_below_context_window(reserve):
    with pytest.raises(ValidationError, match="reserve_tokens"):
        build_config([f"agent.reserve_tokens={reserve}"])


def test_model_identifiers_must_not_be_blank():
    specs = (
        'model.model_name=""',
        'model.model_name=" "',
        'model.api_key_env=""',
        'model.api_key_env=" "',
    )
    for spec in specs:
        with pytest.raises(ValidationError):
            build_config([spec])


@pytest.mark.parametrize("reserved", ["model", "messages", "tools"])
def test_model_kwargs_cannot_override_query_arguments(reserved):
    with pytest.raises(ValidationError, match="model_kwargs"):
        build_config([f'model.model_kwargs={{"{reserved}": "blocked"}}'])


@pytest.mark.parametrize("interpreter", ["[]", '[""]', '["bash", ""]'])
def test_environment_interpreter_must_contain_commands(interpreter):
    with pytest.raises(ValidationError, match="interpreter"):
        build_config([f"environment.interpreter={interpreter}"])

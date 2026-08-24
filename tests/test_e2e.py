"""End-to-end tests — real Model + real Environment, calls an OpenAI-compatible API.

These tests cost money and are slow.  They are skipped by default, including
when an API key happens to be present in the environment.  Run them explicitly
when you want to verify the full agent loop::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_e2e.py -v -m e2e -p no:anyio

Or select them from the complete suite with the marker:

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -v -m e2e -p no:anyio

The repository's pytest configuration adds ``-m 'not e2e'`` by default, so a
plain ``pytest`` command never makes a paid API call.  The explicit ``-m e2e``
above is the opt-in.
"""

import os
import sys
from unittest.mock import Mock

import pytest
from dotenv import load_dotenv

from mini_agent.agent import Agent
from mini_agent.config import Config, build_config
from mini_agent.model import Model
from mini_agent.environments.local import LocalEnvironment


# ---------------------------------------------------------------------------
# skip conditions
# ---------------------------------------------------------------------------

def _load_e2e_config() -> Config:
    """Load the provider-aware configuration used by the E2E tests."""
    load_dotenv()
    model_overrides = {
        config_name: value
        for config_name, env_name in (
            ("model_name", "E2E_MODEL_NAME"),
            ("base_url", "E2E_BASE_URL"),
            ("api_key_env", "E2E_API_KEY_ENV"),
        )
        if (value := os.environ.get(env_name))
    }
    return build_config(cli_overrides={"model": model_overrides})


def _has_api_key(config: Config) -> bool:
    """Check the configured provider's environment variable for a key."""
    return bool(os.environ.get(config.model.api_key_env))


e2e_config = _load_e2e_config()


e2e = pytest.mark.e2e
skip_no_key = pytest.mark.skipif(
    not _has_api_key(e2e_config),
    reason=f"No {e2e_config.model.api_key_env} in .env",
)


def _build_e2e_agent(config: Config):
    """Construct an E2E agent from one fully resolved configuration."""
    return Agent(
        Model(config.model),
        LocalEnvironment(config.environment),
        config=config,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@e2e
@skip_no_key
def test_simple_echo_task():
    """Full agent loop: ask model to echo something and submit.

    This is the simplest possible task — if this fails, nothing works.
    """
    agent = _build_e2e_agent(e2e_config)
    result = agent.run(
        "Run the command 'echo hello from e2e test' and then "
        "submit the output you got from the command."
    )

    assert result["exit_status"] == "submitted", (
        f"Expected submitted, got {result['exit_status']}. "
        f"Messages: {len(result['messages'])}"
    )
    assert "hello from e2e test" in result["submission"], (
        f"Submission should contain the echoed text. "
        f"Got: {result['submission']!r}"
    )

    # Verify the conversation has the expected shape
    roles = [m["role"] for m in result["messages"]]
    assert roles[0] == "system"
    assert roles[1] == "user"
    assert "tool" in roles, "Should have at least one tool call"
    assert roles[-1] == "tool", (
        "Last message should be the submit tool result"
    )


@e2e
@skip_no_key
def test_model_can_use_bash_and_submit(tmp_path, monkeypatch):
    """Verify the model understands both tools and uses submit to exit.

    A slightly harder task: list synthetic files, then submit a summary.
    Running in a temporary directory prevents repository metadata from being
    included in the tool output sent to the external model provider.
    """
    (tmp_path / "alpha.txt").touch()
    (tmp_path / "beta.py").touch()
    monkeypatch.chdir(tmp_path)

    agent = _build_e2e_agent(e2e_config)
    result = agent.run(
        "List the contents of the current working directory, "
        "then submit the list of files you found."
    )

    assert result["exit_status"] == "submitted", (
        f"Expected submitted, got {result['exit_status']}"
    )
    # There should be at least one bash interaction before submit
    tool_names = []
    for m in result["messages"]:
        if m["role"] == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tool_names.append(tc["function"]["name"])
    assert "bash" in tool_names, "Model should have called bash at least once"
    assert "submit" in tool_names, "Model should have submitted"


# ---------------------------------------------------------------------------
# offline regression tests
# ---------------------------------------------------------------------------

def test_has_api_key_uses_configured_provider_environment(monkeypatch):
    """A non-OpenAI provider key should control the E2E skip condition."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MINI_AGENT_MODEL__API_KEY_ENV", raising=False)
    monkeypatch.setenv("CUSTOM_PROVIDER_API_KEY", "configured")
    config = build_config(["model.api_key_env=CUSTOM_PROVIDER_API_KEY"])

    assert _has_api_key(config)

    monkeypatch.delenv("CUSTOM_PROVIDER_API_KEY")
    assert not _has_api_key(config)


def test_load_e2e_config_uses_dedicated_provider_environment(monkeypatch):
    """VS Code's .env values should configure E2E without affecting config."""
    monkeypatch.setenv("E2E_MODEL_NAME", "custom-model")
    monkeypatch.setenv("E2E_BASE_URL", "https://provider.example")
    monkeypatch.setenv("E2E_API_KEY_ENV", "CUSTOM_PROVIDER_API_KEY")

    config = _load_e2e_config()

    assert config.model.model_name == "custom-model"
    assert config.model.base_url == "https://provider.example"
    assert config.model.api_key_env == "CUSTOM_PROVIDER_API_KEY"


def test_e2e_agent_uses_resolved_config_without_network(monkeypatch):
    """E2E assembly passes resolved model, environment, and config objects."""
    config = build_config(
        [
            "model.model_name=custom-model",
            "model.base_url=https://provider.example/v1",
            "model.api_key_env=CUSTOM_PROVIDER_API_KEY",
            "environment.timeout=17",
        ]
    )
    model = object()
    environment = object()
    agent = object()
    model_factory = Mock(return_value=model)
    environment_factory = Mock(return_value=environment)
    agent_factory = Mock(return_value=agent)
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "Model", model_factory)
    monkeypatch.setattr(module, "LocalEnvironment", environment_factory)
    monkeypatch.setattr(module, "Agent", agent_factory)

    assert _build_e2e_agent(config) is agent
    model_factory.assert_called_once_with(config.model)
    environment_factory.assert_called_once_with(config.environment)
    agent_factory.assert_called_once_with(model, environment, config=config)

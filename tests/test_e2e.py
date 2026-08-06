"""End-to-end tests — real Model + real Environment, calls DeepSeek API.

These tests cost money and are slow.  They are skipped by default.
Run them explicitly when you want to verify the full agent loop::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_e2e.py -v -m e2e -p no:anyio

Or use the ``--run-e2e`` marker filter:

    python -m pytest tests/ -v -m e2e
"""

import os

import pytest

from src.mini_agent.agent import Agent
from src.mini_agent.model import Model
from src.mini_agent.environments.local import LocalEnvironment


# ---------------------------------------------------------------------------
# skip conditions
# ---------------------------------------------------------------------------

def _has_api_key() -> bool:
    """Check whether a DeepSeek API key is configured."""
    from dotenv import load_dotenv
    load_dotenv()
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


e2e = pytest.mark.e2e
skip_no_key = pytest.mark.skipif(not _has_api_key(), reason="No DEEPSEEK_API_KEY in .env")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@e2e
@skip_no_key
def test_simple_echo_task():
    """Full agent loop: ask model to echo something and submit.

    This is the simplest possible task — if this fails, nothing works.
    """
    agent = Agent(Model(), LocalEnvironment())
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
def test_model_can_use_bash_and_submit():
    """Verify the model understands both tools and uses submit to exit.

    A slightly harder task: list files, then submit a summary.
    """
    agent = Agent(Model(), LocalEnvironment())
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

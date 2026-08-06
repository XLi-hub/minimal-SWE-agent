"""Integration tests — mock Model + real LocalEnvironment.

Tests the Agent loop with real shell output without calling the API.
This catches encoding issues, empty output handling, and real command
interactions that mock return_values can't surface.
"""

from unittest.mock import MagicMock

from src.mini_agent.agent import Agent
from src.mini_agent.environments.local import LocalEnvironment


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_response(content=None, tool_calls=None):
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(id_: str, name: str, arguments: dict):
    """Build a mock tool call object."""
    import json
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


# ---------------------------------------------------------------------------
# basic integration
# ---------------------------------------------------------------------------

def test_agent_executes_real_echo_and_submits():
    """Agent loop with real shell: echo something → model reads it → submits."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me check.",
            tool_calls=[
                _make_tool_call("c1", "bash", {"command": "echo hello integration"}),
            ],
        ),
        _make_response(
            content="Got it.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "done"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("echo and submit", max_steps=5)

    assert result["exit_status"] == "submitted"

    # Check that the real output was captured in messages
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    outputs = [m["content"] for m in tool_msgs]
    assert any("hello integration" in o for o in outputs), (
        f"Real shell output not found in: {outputs}"
    )


def test_agent_sees_real_ls_output():
    """Verify the agent loop correctly passes real 'ls' output back to model."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Listing files.",
            tool_calls=[
                _make_tool_call("c1", "bash", {"command": "ls tests/"}),
            ],
        ),
        _make_response(
            content="Done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "ls done"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("list tests directory", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    # Real ls output should mention our test files
    ls_output = tool_msgs[0]["content"]
    assert "test_agent" in ls_output, f"Expected test files in output: {ls_output!r}"


# ---------------------------------------------------------------------------
# real error handling
# ---------------------------------------------------------------------------

def test_agent_records_real_command_failure():
    """Non-zero exit from real shell → error output captured, agent continues."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me try a bad command.",
            tool_calls=[
                _make_tool_call("c1", "bash", {"command": "cat /nonexistent/file.txt"}),
            ],
        ),
        _make_response(
            content="That failed. Done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "failed gracefully"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("test error handling", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    # Real stderr should appear in the output (merged into stdout)
    error_output = tool_msgs[0]["content"]
    assert len(error_output) > 0, "Error output should not be empty"


# ---------------------------------------------------------------------------
# truncation with real output
# ---------------------------------------------------------------------------

def test_truncation_with_real_multiline_output():
    """Real command with lots of output should be truncated."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Counting.",
            tool_calls=[
                # Print 300 lines — should trigger truncation (default lines=100)
                _make_tool_call(
                    "c1", "bash",
                    {"command": "seq 1 300"},
                ),
            ],
        ),
        _make_response(
            content="Seen enough.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "ok"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("print numbers", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    truncated = tool_msgs[0]["content"]
    assert "truncated" in truncated, f"Should be truncated: {truncated!r}"
    assert "1" in truncated, "Should include first line"
    assert "300" in truncated, "Should include last line"


# ---------------------------------------------------------------------------
# multi-step real interaction
# ---------------------------------------------------------------------------

def test_multi_step_real_commands():
    """Multiple real commands in sequence → agent accumulates output correctly."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Step 1: pwd.",
            tool_calls=[
                _make_tool_call("c1", "bash", {"command": "pwd"}),
            ],
        ),
        _make_response(
            content="Step 2: echo.",
            tool_calls=[
                _make_tool_call("c2", "bash", {"command": "echo step2 done"}),
            ],
        ),
        _make_response(
            content="All done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "multi-step complete"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("multi-step task", max_steps=10)

    assert result["exit_status"] == "submitted"

    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 3, f"Expected 3 tool messages (bash+bash+submit), got {len(tool_msgs)}"
    # First tool output should be a valid path
    assert "/" in tool_msgs[0]["content"], "pwd should return an absolute path"
    assert "step2 done" in tool_msgs[1]["content"], "echo output mismatch"
    assert tool_msgs[2]["content"] == "Submitted."


# ---------------------------------------------------------------------------
# realistic agent scenarios
# ---------------------------------------------------------------------------

def test_git_diff_output():
    """git diff produces multi-line output with +/- markers — agent should
    preserve it verbatim so the model sees a real patch."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me see what changed.",
            tool_calls=[
                # diff two test files — guaranteed to produce real output
                _make_tool_call(
                    "c1", "bash",
                    {"command": "diff -u tests/test_agent.py tests/test_config.py"},
                ),
            ],
        ),
        _make_response(
            content="Here is the diff.",
            tool_calls=[
                _make_tool_call(
                    "s1", "submit",
                    {"output": "diff analyzed"},
                ),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("show diff", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    diff_output = tool_msgs[0]["content"]

    # Real diff output has headers, hunks, +/- lines
    assert "--- " in diff_output or "+++ " in diff_output or "@@" in diff_output, (
        f"Expected diff markers in output: {diff_output[:200]}"
    )


def test_python_execution_output():
    """Running python -c with print — testing real code execution in shell."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me run a quick Python check.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    {"command": "python -c 'import sys; print(sys.version[:10])'"},
                ),
            ],
        ),
        _make_response(
            content="Got version.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "version checked"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("check python version", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    # Should have actual Python version string like "3.10.20"
    python_output = tool_msgs[0]["content"]
    assert any(c.isdigit() for c in python_output), (
        f"Expected Python version in output: {python_output!r}"
    )


def test_find_command_large_output():
    """find across a directory tree can produce hundreds of lines —
    agent must handle truncation correctly with real paths."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me find all Python files.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    {"command": "find src/ -name '*.py'", "lines": 10},
                ),
            ],
        ),
        _make_response(
            content="Found the files.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "files found"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("find python files", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    find_output = tool_msgs[0]["content"]

    assert "agent.py" in find_output or "config.py" in find_output, (
        f"Expected to find project .py files: {find_output!r}"
    )


def test_empty_output_command():
    """Some commands produce nothing on stdout — agent should not crash."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me try a no-op.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    {"command": "true"},  # exit 0, no output
                ),
            ],
        ),
        _make_response(
            content="Done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "no-op complete"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("no-op test", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    # Empty output should be fine — just an empty string
    bash_output = tool_msgs[0]["content"]
    assert bash_output == "", f"Expected empty output for 'true': {bash_output!r}"


def test_stderr_stdout_mixed():
    """Commands that write to both stdout and stderr — agent merges them,
    so the model sees everything.  Real SWE agents rely on this to see errors."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Testing mixed output.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    {"command": (
                        "python -c '"
                        "import sys; "
                        "sys.stdout.write(\"stdout message\\n\"); "
                        "sys.stderr.write(\"stderr message\\n\")"
                        "'"
                    )},
                ),
            ],
        ),
        _make_response(
            content="Both streams captured.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "mixed streams ok"}),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("test stderr mixing", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    mixed = tool_msgs[0]["content"]
    # Both stdout and stderr should be present (merged)
    assert "stdout message" in mixed, f"Missing stdout: {mixed!r}"
    assert "stderr message" in mixed, f"Missing stderr: {mixed!r}"


def test_command_with_special_characters():
    """Output with backslashes, quotes, and unicode should survive the
    parsing/serialization round-trip through the Agent loop."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Echo special chars.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    # printf doesn't interpret backslash sequences like echo does
                    {"command": "printf '%s' 'path\\to\\file  \"quoted\"  ünicode'"},
                ),
            ],
        ),
        _make_response(
            content="Special chars preserved.",
            tool_calls=[
                _make_tool_call(
                    "s1", "submit",
                    {"output": "special chars survived"},
                ),
            ],
        ),
    ]

    agent = Agent(model, LocalEnvironment())
    result = agent.run("special chars test", max_steps=5)

    assert result["exit_status"] == "submitted"
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    output = tool_msgs[0]["content"]
    # Backslashes and quotes should survive the trip unchanged
    assert "path\\to\\file" in output, f"Backslashes lost: {output!r}"
    assert '"quoted"' in output, f"Quotes lost: {output!r}"
    assert "ünicode" in output, f"Unicode lost: {output!r}"

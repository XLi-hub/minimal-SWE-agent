"""Tests for the read/edit/write tool logic and dispatch."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest

import mini_agent.tools as tools_module
from mini_agent.config import build_config, get_default_config
from mini_agent.tools import (
    DEFAULT_MAX_CHARS,
    EditError,
    TOOL_REGISTRY,
    decode_timeout_output,
    apply_edit,
    execute_tool_call,
    find_network_command,
    format_execution_observation,
    format_read_output,
    get_enabled_tool_schemas,
    truncate_output,
)
from mini_agent.tooling.files import (
    EditError as ExtractedEditError,
    apply_edit as extracted_apply_edit,
    format_read_output as extracted_format_read_output,
)
from mini_agent.tooling.network import find_network_command as extracted_find_network_command
from mini_agent.tooling.output import (
    DEFAULT_MAX_CHARS as EXTRACTED_DEFAULT_MAX_CHARS,
    decode_timeout_output as extracted_decode_timeout_output,
    format_execution_observation as extracted_format_execution_observation,
    truncate_output as extracted_truncate_output,
)
from mini_agent.tooling.schemas import (
    BASH_SCHEMA as EXTRACTED_BASH_SCHEMA,
    EDIT_SCHEMA as EXTRACTED_EDIT_SCHEMA,
    READ_SCHEMA as EXTRACTED_READ_SCHEMA,
    SUBMIT_SCHEMA as EXTRACTED_SUBMIT_SCHEMA,
    TRAJECTORY_SCHEMA as EXTRACTED_TRAJECTORY_SCHEMA,
    WRITE_SCHEMA as EXTRACTED_WRITE_SCHEMA,
)
from mini_agent.tooling.types import (
    ToolContext as ExtractedToolContext,
    ToolDefinition as ExtractedToolDefinition,
    ToolHandler as ExtractedToolHandler,
    ToolResult as ExtractedToolResult,
)


def _tc(id_: str, name: str, arguments: dict):
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


class FakeEnv:
    """Minimal in-memory environment recording read_file/write_file calls."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []

    def read_file(self, path):
        self.read_calls.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file(self, path, content):
        self.write_calls.append((path, content))
        self.files[path] = content

    def execute(self, command, **kwargs):
        return f"output of {command}"


def test_tools_keeps_legacy_exports_and_handler_patch_surface(monkeypatch):
    """Extracted helpers remain available at the historical module path."""
    assert DEFAULT_MAX_CHARS is EXTRACTED_DEFAULT_MAX_CHARS
    assert EditError is ExtractedEditError
    assert apply_edit is extracted_apply_edit
    assert format_read_output is extracted_format_read_output
    assert find_network_command is extracted_find_network_command
    assert format_execution_observation is extracted_format_execution_observation
    assert truncate_output is extracted_truncate_output
    assert decode_timeout_output is extracted_decode_timeout_output
    assert tools_module.ToolContext is ExtractedToolContext
    assert tools_module.ToolResult is ExtractedToolResult
    assert tools_module.ToolDefinition is ExtractedToolDefinition
    assert tools_module.ToolHandler is ExtractedToolHandler
    assert tools_module.BASH_SCHEMA is EXTRACTED_BASH_SCHEMA
    assert tools_module.SUBMIT_SCHEMA is EXTRACTED_SUBMIT_SCHEMA
    assert tools_module.READ_SCHEMA is EXTRACTED_READ_SCHEMA
    assert tools_module.EDIT_SCHEMA is EXTRACTED_EDIT_SCHEMA
    assert tools_module.WRITE_SCHEMA is EXTRACTED_WRITE_SCHEMA
    assert tools_module.TRAJECTORY_SCHEMA is EXTRACTED_TRAJECTORY_SCHEMA

    monkeypatch.setattr(
        tools_module,
        "format_execution_observation",
        lambda result, max_lines, max_chars: "patched observation",
    )
    messages: list = []
    execute_tool_call(_tc("patch", "bash", {"command": "echo hi"}), messages, FakeEnv())
    assert messages[0]["content"] == "patched observation"


# --- apply_edit (pure) ---


def test_apply_edit_replaces_single_occurrence():
    assert apply_edit("a = 1\nb = 2\n", "a = 1", "a = 3") == "a = 3\nb = 2\n"


def test_apply_edit_absent_raises():
    with pytest.raises(EditError):
        apply_edit("a = 1\n", "zzz", "x")


def test_apply_edit_ambiguous_raises():
    with pytest.raises(EditError):
        apply_edit("x = 1\nx = 1\n", "x = 1", "x = 2")


def test_apply_edit_empty_old_raises():
    with pytest.raises(EditError):
        apply_edit("abc", "", "x")


def test_apply_edit_empty_new_deletes():
    assert apply_edit("a\nb\n", "a\n", "") == "b\n"


def test_apply_edit_multiline():
    content = "def f():\n    return 1\n"
    assert apply_edit(content, "    return 1", "    return 2") == (
        "def f():\n    return 2\n"
    )


# --- format_read_output (pure) ---


def test_format_read_output_numbers_lines():
    assert format_read_output("a\nb") == "     1\ta\n     2\tb"


def test_format_read_output_empty():
    assert format_read_output("") == "(empty file)"


def test_format_read_output_returns_requested_source_chunk():
    content = "\n".join(f"line {number}" for number in range(1, 301))

    output = format_read_output(content, start_line=101, max_lines=3)

    assert output.startswith("   101\tline 101")
    assert "   103\tline 103" in output
    assert "line 104" not in output
    assert "line_start=104" in output


def test_format_read_output_has_character_budget_for_one_long_line():
    content = "开头" + "中" * (DEFAULT_MAX_CHARS * 2) + "结尾"
    output = format_read_output(content, max_chars=256)

    assert len(output) <= 256
    assert output.startswith("     1\t开头")
    assert "characters truncated" in output
    assert output.endswith("结尾")


def test_truncate_output_preserves_unicode_head_and_tail():
    output = truncate_output("😀" * 500 + "终点", max_lines=100, max_chars=256)

    assert len(output) <= 256
    assert output.startswith("😀")
    assert output.endswith("终点")
    assert "characters truncated" in output


# --- dispatch ---


def test_read_returns_numbered_content():
    env = FakeEnv(files={"f.py": "x = 1\n"})
    messages: list = []
    execute_tool_call(
        _tc("c1", "read", {"path": "f.py"}), messages, env
    )
    assert messages[0]["role"] == "tool"
    assert "     1\tx = 1" in messages[0]["content"]


def test_read_uses_configured_default_line_limit_and_supports_continuation():
    content = "\n".join(f"line {number}" for number in range(1, 301))
    env = FakeEnv(files={"large.py": content})
    messages: list = []

    execute_tool_call(_tc("first", "read", {"path": "large.py"}), messages, env)
    execute_tool_call(
        _tc("next", "read", {"path": "large.py", "line_start": 101, "lines": 2}),
        messages,
        env,
    )

    assert "   100\tline 100" in messages[0]["content"]
    assert "line 101" not in messages[0]["content"]
    assert "line_start=101" in messages[0]["content"]
    assert messages[1]["content"].startswith("   101\tline 101")
    assert "   102\tline 102" in messages[1]["content"]
    assert "line 103" not in messages[1]["content"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "f.py", "line_start": 0},
        {"path": "f.py", "line_start": True},
        {"path": "f.py", "lines": 0},
        {"path": "f.py", "lines": "10"},
    ],
)
def test_read_rejects_invalid_line_ranges(arguments):
    messages: list = []

    execute_tool_call(_tc("read", "read", arguments), messages, FakeEnv())

    assert messages[0]["content"].startswith("Error: 'read'")


def test_read_missing_file_is_error_message():
    env = FakeEnv()
    messages: list = []
    execute_tool_call(
        _tc("c1", "read", {"path": "nope"}), messages, env
    )
    assert "file not found" in messages[0]["content"]


def test_edit_calls_read_and_write():
    env = FakeEnv(files={"f.py": "x = 1\n"})
    messages: list = []
    execute_tool_call(
        _tc(
            "c1",
            "edit",
            {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
        ),
        messages,
        env,
    )
    assert env.read_calls == ["f.py"]
    assert env.write_calls == [("f.py", "x = 2\n")]
    assert messages[0]["content"].startswith("Edited f.py")


def test_edit_ambiguous_does_not_write():
    env = FakeEnv(files={"f.py": "x = 1\nx = 1\n"})
    messages: list = []
    execute_tool_call(
        _tc(
            "c1",
            "edit",
            {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
        ),
        messages,
        env,
    )
    assert env.write_calls == []
    assert "ambiguous" in messages[0]["content"]


def test_write_empty_content_succeeds():
    env = FakeEnv()
    messages: list = []
    execute_tool_call(
        _tc("c1", "write", {"path": "empty.txt", "content": ""}),
        messages,
        env,
    )
    assert env.write_calls == [("empty.txt", "")]
    assert "Wrote empty.txt" in messages[0]["content"]


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


def test_bash_observation_preserves_execution_metadata():
    observation = format_execution_observation(
        {
            "output": "line 1\nline 2\n",
            "returncode": 7,
            "exception_info": "",
        },
        max_lines=100,
    )
    assert json.loads(observation) == {
        "output": "line 1\nline 2\n",
        "returncode": 7,
        "exception_info": "",
    }


def test_bash_observation_truncates_structured_output():
    observation = format_execution_observation(
        {
            "output": "\n".join(f"line {i}" for i in range(20)),
            "returncode": 0,
            "exception_info": "",
        },
        max_lines=4,
    )
    parsed = json.loads(observation)
    assert "lines truncated" in parsed["output"]
    assert parsed["returncode"] == 0


def test_bash_observation_character_budget_covers_single_line():
    observation = format_execution_observation(
        {
            "output": "首" * 100_000 + "尾",
            "returncode": 0,
            "exception_info": "",
        },
        max_lines=100,
        max_chars=256,
    )

    assert len(observation) <= 256
    parsed = json.loads(observation)
    assert "characters truncated" in parsed["output"]
    assert parsed["output"].startswith("首")
    assert parsed["output"].endswith("尾")


def test_handlers_use_configured_character_budget():
    cfg = build_config(["tools.default_max_chars=128"])
    env = FakeEnv(files={"long.txt": "首" * 10_000 + "尾"})
    messages: list = []

    execute_tool_call(_tc("read-1", "read", {"path": "long.txt"}), messages, env, cfg)

    assert len(messages[0]["content"]) <= 128
    assert "characters truncated" in messages[0]["content"]


def test_bash_timeout_observation_respects_final_character_budget():
    cfg = build_config(["tools.default_max_chars=256"])
    env = FakeEnv()
    timeout = subprocess.TimeoutExpired("slow", 3)
    timeout.stdout = "partial output\n" + "x" * 10_000
    env.execute = MagicMock(side_effect=timeout)
    messages: list = []

    execute_tool_call(_tc("timeout-1", "bash", {"command": "slow"}), messages, env, cfg)

    assert len(messages[0]["content"]) <= 256


def test_bash_exception_observation_respects_final_character_budget():
    cfg = build_config(["tools.default_max_chars=128"])
    env = FakeEnv()
    env.execute = MagicMock(side_effect=RuntimeError("x" * 10_000))
    messages: list = []

    execute_tool_call(_tc("error-1", "bash", {"command": "boom"}), messages, env, cfg)

    assert len(messages[0]["content"]) <= 128


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


def test_registry_pairs_every_name_with_matching_schema():
    for name, definition in TOOL_REGISTRY.items():
        assert definition.name == name
        assert definition.schema["function"]["name"] == name
        assert callable(definition.handler)


def test_enabled_schemas_follow_configured_order():
    cfg = build_config(["default_bash"])
    schemas = get_enabled_tool_schemas(cfg)
    assert [schema["function"]["name"] for schema in schemas] == ["bash", "submit"]


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


def test_unregistered_configured_tool_is_rejected():
    cfg = build_config(['tools.enabled=["bash","missing"]'])
    with pytest.raises(ValueError, match="not registered"):
        get_enabled_tool_schemas(cfg)

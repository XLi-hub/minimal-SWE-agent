"""Tests for the read/edit/write tool logic and dispatch."""

import json
from unittest.mock import MagicMock

import pytest

from mini_agent.config import build_config, get_default_config
from mini_agent.tools import (
    EditError,
    TOOL_REGISTRY,
    apply_edit,
    execute_tool_call,
    format_execution_observation,
    format_read_output,
    get_enabled_tool_schemas,
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


# --- dispatch ---


def test_read_returns_numbered_content():
    env = FakeEnv(files={"f.py": "x = 1\n"})
    messages: list = []
    execute_tool_call(
        _tc("c1", "read", {"path": "f.py"}), messages, env
    )
    assert messages[0]["role"] == "tool"
    assert "     1\tx = 1" in messages[0]["content"]


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


def test_unregistered_configured_tool_is_rejected():
    cfg = build_config(['tools.enabled=["bash","missing"]'])
    with pytest.raises(ValueError, match="not registered"):
        get_enabled_tool_schemas(cfg)

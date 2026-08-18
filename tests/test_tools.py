"""Tests for the read/edit/write tool logic and dispatch."""

import json
from unittest.mock import MagicMock

import pytest

from src.mini_agent.config import get_default_config
from src.mini_agent.tools import (
    EditError,
    apply_edit,
    execute_tool_call,
    format_read_output,
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
    result: dict = {}
    exited = execute_tool_call(
        _tc("c1", "read", {"path": "f.py"}), messages, result, env
    )
    assert exited is False
    assert messages[0]["role"] == "tool"
    assert "     1\tx = 1" in messages[0]["content"]


def test_read_missing_file_is_error_message():
    env = FakeEnv()
    messages: list = []
    result: dict = {}
    execute_tool_call(
        _tc("c1", "read", {"path": "nope"}), messages, result, env
    )
    assert "file not found" in messages[0]["content"]


def test_edit_calls_read_and_write():
    env = FakeEnv(files={"f.py": "x = 1\n"})
    messages: list = []
    result: dict = {}
    execute_tool_call(
        _tc(
            "c1",
            "edit",
            {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
        ),
        messages,
        result,
        env,
    )
    assert env.read_calls == ["f.py"]
    assert env.write_calls == [("f.py", "x = 2\n")]
    assert messages[0]["content"].startswith("Edited f.py")


def test_edit_ambiguous_does_not_write():
    env = FakeEnv(files={"f.py": "x = 1\nx = 1\n"})
    messages: list = []
    result: dict = {}
    execute_tool_call(
        _tc(
            "c1",
            "edit",
            {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"},
        ),
        messages,
        result,
        env,
    )
    assert env.write_calls == []
    assert "ambiguous" in messages[0]["content"]


def test_write_empty_content_succeeds():
    env = FakeEnv()
    messages: list = []
    result: dict = {}
    execute_tool_call(
        _tc("c1", "write", {"path": "empty.txt", "content": ""}),
        messages,
        result,
        env,
    )
    assert env.write_calls == [("empty.txt", "")]
    assert "Wrote empty.txt" in messages[0]["content"]


def test_unknown_tool_lists_all_available_tools():
    cfg = get_default_config()
    env = FakeEnv()
    messages: list = []
    result: dict = {}
    execute_tool_call(
        _tc("c1", "frobnicate", {}), messages, result, env, config=cfg
    )
    content = messages[0]["content"]
    for name in ("bash", "submit", "read", "edit", "write"):
        assert name in content

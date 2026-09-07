"""Tests for file editing and read/write tool behavior."""

import pytest

from mini_agent.tools import (
    DEFAULT_MAX_CHARS,
    EditError,
    apply_edit,
    execute_tool_call,
    format_read_output,
)

from ._helpers import FakeEnv, _tc


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

"""Tests for tool output formatting, truncation, and error observations."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from mini_agent.config import build_config
from mini_agent.tools import (
    decode_timeout_output,
    execute_tool_call,
    format_execution_observation,
    truncate_output,
)

from ._helpers import FakeEnv, _tc


def test_truncate_output_preserves_unicode_head_and_tail():
    output = truncate_output("😀" * 500 + "终点", max_lines=100, max_chars=256)

    assert len(output) <= 256
    assert output.startswith("😀")
    assert output.endswith("终点")
    assert "characters truncated" in output


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


# --- pure output truncation ---

class TestTruncateOutput:
    """Tests for truncate_output."""

    def test_short_output_passes_through(self):
        output = "line 1\nline 2\nline 3"
        assert truncate_output(output, max_lines=10) == output

    def test_exactly_at_limit_passes_through(self):
        output = "\n".join(str(i) for i in range(10))
        assert truncate_output(output, max_lines=10) == output

    def test_long_output_is_truncated(self):
        lines = [f"line {i}" for i in range(200)]
        output = "\n".join(lines)
        result = truncate_output(output, max_lines=100)
        result_lines = result.splitlines()
        assert len(result_lines) == 102  # 50 head + 2 (marker+warning) + 50 tail
        assert result_lines[0] == "line 0"
        assert result_lines[-1] == "line 199"
        assert any("100 lines truncated" in line for line in result_lines)

    def test_truncation_includes_elision_info(self):
        lines = [f"L{i:04d}" for i in range(500)]
        output = "\n".join(lines)
        result = truncate_output(output, max_lines=50)
        assert "450 lines truncated" in result
        assert "500 total" in result
        assert "50 shown" in result

    def test_minimum_lines_is_2(self):
        output = "\n".join(str(i) for i in range(10))
        result = truncate_output(output, max_lines=1)
        result_lines = result.splitlines()
        assert len(result_lines) == 4  # 1 head + 2 (marker+warning) + 1 tail
        assert "8 lines truncated" in result

    def test_lines_zero_is_clamped(self):
        output = "a\nb\nc\nd\ne"
        result = truncate_output(output, max_lines=0)
        result_lines = result.splitlines()
        assert len(result_lines) == 4  # 1 head + 2 (marker+warning) + 1 tail
        assert result_lines[0] == "a"
        assert result_lines[-1] == "e"
        assert "WARNING" in result

    @pytest.mark.parametrize("max_chars", range(0, 9))
    def test_character_budget_is_never_exceeded_for_tiny_limits(self, max_chars):
        result = truncate_output("头" * 100 + "尾", max_lines=100, max_chars=max_chars)
        assert len(result) <= max_chars

# --- timeout output decoding ---

class TestDecodeTimeoutOutput:
    """Tests for decode_timeout_output."""

    def test_decodes_bytes_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = b"partial output line 1\npartial line 2\n"
        result = decode_timeout_output(exc)
        assert "partial output line 1" in result
        assert "partial line 2" in result

    def test_handles_none_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = None
        result = decode_timeout_output(exc)
        assert "no output before timeout" in result.lower()

    def test_passes_through_string_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = "already a string"
        result = decode_timeout_output(exc)
        assert result == "already a string"

    def test_decodes_with_bad_encoding(self):
        exc = subprocess.TimeoutExpired(cmd="cat broken.bin", timeout=5)
        exc.stdout = b"valid start \xff\xfe bad bytes"
        result = decode_timeout_output(exc)
        assert "valid start" in result

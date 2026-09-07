"""Compatibility tests for the historical tools module surface."""

from unittest.mock import MagicMock

from mini_agent import tools as tools_module
from mini_agent.tools import (
    DEFAULT_MAX_CHARS,
    EditError,
    apply_edit,
    decode_timeout_output,
    execute_tool_call,
    format_assistant_message,
    find_network_command,
    format_execution_observation,
    format_read_output,
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

from ._helpers import FakeEnv, _tc


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


# --- format_assistant_message ---


def testformat_assistant_message_with_tool_calls():
    """验证 format_assistant_message 输出正确的 dict 结构。"""
    msg = MagicMock()
    msg.content = "I will run a command."

    tc = MagicMock()
    tc.id = "call_42"
    tc.function.name = "bash"
    tc.function.arguments = '{"command": "ls"}'
    msg.tool_calls = [tc]

    result = format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "I will run a command."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "call_42"
    assert result["tool_calls"][0]["type"] == "function"
    assert result["tool_calls"][0]["function"]["name"] == "bash"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def testformat_assistant_message_without_tool_calls():
    """无 tool_calls 时应返回空列表。"""
    msg = MagicMock()
    msg.content = "Hello."
    msg.tool_calls = []

    result = format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "Hello."
    assert result["tool_calls"] == []

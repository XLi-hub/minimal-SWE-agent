"""Tool execution, output formatting, and truncation utilities.

These are extracted from ``agent.py`` so the agent loop stays focused on
orchestration, while tool-specific logic lives in its own module.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from mini_agent.config import Config, get_default_config
from mini_agent.exceptions import Submitted


# ---------------------------------------------------------------------------
# registry types and tool schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    """Dependencies shared by tool handlers."""

    environment: Any
    config: Config


@dataclass(frozen=True)
class ToolResult:
    """A handler result; ``submission`` requests a successful agent exit."""

    content: str
    submission: str | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    """Everything needed to advertise and execute one tool."""

    name: str
    schema: dict[str, Any]
    handler: ToolHandler

    def __post_init__(self) -> None:
        schema_name = self.schema.get("function", {}).get("name")
        if schema_name != self.name:
            raise ValueError(
                f"tool registry name {self.name!r} does not match schema "
                f"name {schema_name!r}"
            )


BASH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a bash command in the terminal and return its output. Use "
            "the optional 'lines' parameter to limit how many lines are returned "
            "(default 100). The output is truncated when it exceeds this limit — "
            "if you need more context, re-run with a higher 'lines' value or use "
            "head/tail/sed to narrow down. Use the optional 'timeout' parameter "
            "(seconds, default 30) for commands that need more time — e.g. pip "
            "install or git clone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "lines": {
                    "type": "integer",
                    "description": (
                        "Maximum lines of output to return (default 100). Set "
                        "higher for more context, lower to save tokens."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Maximum seconds to wait for the command (default 30). "
                        "Set higher for slow commands like pip install, git "
                        "clone, or long builds."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

SUBMIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": (
            "Submit your final answer when the task is complete. Call this once "
            "you have finished all necessary work — pass your patch, answer, or "
            "summary as the output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "output": {
                    "type": "string",
                    "description": "Final answer, patch, or summary of what was done.",
                },
            },
            "required": ["output"],
        },
    },
}

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read",
        "description": (
            "Read a file's contents and return them with line numbers. Use this "
            "to inspect code before editing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the file to read (relative to the working "
                        "directory, or absolute)."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

EDIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": (
            "Replace exactly one occurrence of old_string in a file with "
            "new_string. Fails if old_string is absent or ambiguous (more than "
            "one match) — include surrounding context to make it unique."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {
                    "type": "string",
                    "description": (
                        "The exact text to replace (must match exactly once, "
                        "including whitespace)."
                    ),
                },
                "new_string": {
                    "type": "string",
                    "description": (
                        "The replacement text. An empty string deletes old_string."
                    ),
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write",
        "description": (
            "Create a new file or overwrite an existing file with the given content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write."},
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
}


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _handle_submit(args: dict[str, Any], context: ToolContext) -> ToolResult:
    submission = args.get("output", "")
    print("Submit:", submission)
    return ToolResult("Submitted.", submission=submission)


def _handle_bash(args: dict[str, Any], context: ToolContext) -> ToolResult:
    command = args.get("command")
    if not command:
        return ToolResult("Error: 'bash' requires a 'command' argument.")

    defaults = context.config.tools
    max_lines = args.get("lines", defaults.default_max_lines)
    timeout = args.get("timeout", defaults.default_timeout)
    print("Action:", command)

    try:
        raw = context.environment.execute(command, timeout=timeout)
        output = truncate_output(raw, max_lines)
    except subprocess.TimeoutExpired as exc:
        partial = decode_timeout_output(exc)
        output = (
            f"{truncate_output(partial, max_lines)}\n"
            f"[STILL RUNNING: Command has been executing for "
            f"{timeout}s and is not finished yet. The process "
            f"is still alive. To wait for it, re-run with a "
            f"higher 'timeout' (e.g. timeout={timeout * 2}). "
            f"To abort and restart, kill the old process first "
            f"(use 'ps aux | grep' to find its PID, then 'kill'). "
            f"Do NOT re-run without killing — two instances "
            f"of the same command will conflict.]"
        )
    except Exception as exc:
        output = f"Error: {exc}"

    print("Output:", output)
    return ToolResult(output)


def _handle_read(args: dict[str, Any], context: ToolContext) -> ToolResult:
    path = args.get("path")
    if not path:
        return ToolResult("Error: 'read' requires a 'path' argument.")

    print("Read:", path)
    try:
        content = context.environment.read_file(path)
    except FileNotFoundError:
        output = f"Error: file not found: {path}"
    except IsADirectoryError:
        output = f"Error: {path} is a directory, not a file."
    except Exception as exc:
        output = f"Error: {exc}"
    else:
        output = format_read_output(content)
    print("Output:", output)
    return ToolResult(output)


def _handle_edit(args: dict[str, Any], context: ToolContext) -> ToolResult:
    path = args.get("path")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not path or old_string is None or new_string is None:
        return ToolResult(
            "Error: 'edit' requires 'path', 'old_string', and 'new_string'."
        )

    print("Edit:", path)
    try:
        original = context.environment.read_file(path)
    except FileNotFoundError:
        output = f"Error: file not found: {path}"
    except Exception as exc:
        output = f"Error: {exc}"
    else:
        try:
            updated = apply_edit(original, old_string, new_string)
        except EditError as exc:
            output = f"Error: {exc}"
        else:
            try:
                context.environment.write_file(path, updated)
            except Exception as exc:
                output = f"Error: {exc}"
            else:
                output = (
                    f"Edited {path}: replaced the unique occurrence of:\n"
                    f"--- old ---\n{old_string}\n--- new ---\n{new_string}"
                )
    print("Output:", output)
    return ToolResult(output)


def _handle_write(args: dict[str, Any], context: ToolContext) -> ToolResult:
    path = args.get("path")
    content = args.get("content")
    if not path or content is None:
        return ToolResult("Error: 'write' requires 'path' and 'content'.")

    print("Write:", path)
    try:
        context.environment.write_file(path, content)
    except Exception as exc:
        output = f"Error: {exc}"
    else:
        output = f"Wrote {path} ({len(content)} characters)."
    print("Output:", output)
    return ToolResult(output)


def _build_registry(*definitions: ToolDefinition) -> dict[str, ToolDefinition]:
    registry: dict[str, ToolDefinition] = {}
    for definition in definitions:
        if definition.name in registry:
            raise ValueError(f"duplicate tool registration: {definition.name!r}")
        registry[definition.name] = definition
    return registry


TOOL_REGISTRY = _build_registry(
    ToolDefinition("bash", BASH_SCHEMA, _handle_bash),
    ToolDefinition("submit", SUBMIT_SCHEMA, _handle_submit),
    ToolDefinition("read", READ_SCHEMA, _handle_read),
    ToolDefinition("edit", EDIT_SCHEMA, _handle_edit),
    ToolDefinition("write", WRITE_SCHEMA, _handle_write),
)
"""Explicit registry: one source of truth for every schema/handler pair."""


# ---------------------------------------------------------------------------
# tool selection and dispatch
# ---------------------------------------------------------------------------


def get_enabled_tool_schemas(config: Config | None = None) -> list[dict[str, Any]]:
    """Return schemas for the configured tools, preserving configured order."""
    cfg = config or get_default_config()
    schemas: list[dict[str, Any]] = []
    for name in cfg.tools.enabled:
        definition = TOOL_REGISTRY.get(name)
        if definition is None:
            registered = ", ".join(TOOL_REGISTRY)
            raise ValueError(
                f"configured tool {name!r} is not registered; "
                f"registered tools: {registered}"
            )
        schemas.append(definition.schema)
    return schemas


def execute_tool_call(tc, messages: list[dict], environment,
                      config: Config | None = None) -> None:
    """Validate, dispatch, and record one OpenAI tool call.

    Parameters
    ----------
    tc:
        An OpenAI ``ToolCall`` object with ``.id``, ``.function.name``,
        and ``.function.arguments``.
    messages:
        The message history (mutated in place with tool results).
    environment:
        An execution environment with ``.execute(command, timeout) -> str``.
    config:
        Optional :class:`Config`. Its ``tools.enabled`` list controls both
        model visibility and execution permission.

    Raises
    ------
    Submitted
        When the tool is ``submit`` (the run completed with an answer).
    """
    cfg = config or get_default_config()
    name = tc.function.name
    definition = TOOL_REGISTRY.get(name)

    if definition is None:
        available = ", ".join(cfg.tools.tool_names())
        result = ToolResult(
            f"Error: unknown tool '{name}'. Available tools: {available}."
        )
    elif name not in cfg.tools.enabled:
        enabled = ", ".join(cfg.tools.tool_names())
        result = ToolResult(
            f"Error: tool '{name}' is disabled. Enabled tools: {enabled}."
        )
    else:
        try:
            args = json.loads(tc.function.arguments)
            if not isinstance(args, dict):
                raise ValueError("arguments must decode to a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            result = ToolResult(f"Error: invalid arguments for '{name}': {exc}")
        else:
            try:
                result = definition.handler(args, ToolContext(environment, cfg))
            except Exception as exc:
                result = ToolResult(f"Error: {exc}")

    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": result.content,
    })

    if result.submission is not None:
        raise Submitted(result.submission)
    return None


# ---------------------------------------------------------------------------
# message formatting
# ---------------------------------------------------------------------------


def format_assistant_message(msg) -> dict:
    """Convert an OpenAI message object to the dict format for the API."""
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ],
    }


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def decode_timeout_output(exc: subprocess.TimeoutExpired) -> str:
    """Extract partial output from a :class:`subprocess.TimeoutExpired` exception.

    Returns the captured stdout as a string, or a placeholder if nothing
    was captured before the timeout.
    """
    raw = exc.stdout
    if raw is None:
        return "(no output before timeout)"
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def truncate_output(output: str, max_lines: int) -> str:
    """Truncate *output* to at most *max_lines* lines.

    When truncation happens the first ``max_lines // 2`` and last
    ``max_lines // 2`` lines are kept with an elision marker in between,
    so the model sees both the beginning and the end of the output.
    """
    if max_lines < 2:
        max_lines = 2  # minimum: 1 head + 1 tail

    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output

    half = max(1, max_lines // 2)
    head = lines[:half]
    tail = lines[-half:]
    elided = len(lines) - max_lines

    warning = (
        f"[... {elided} lines truncated ({len(lines)} total, {max_lines} shown) ...]\n"
        f"[WARNING: Output was truncated. To see more, re-run with a higher "
        f"'lines' value (e.g. lines={len(lines)}), or use head/tail/sed to "
        f"narrow down the output.]"
    )
    return "\n".join(head + [warning] + tail)


class EditError(ValueError):
    """Raised when an edit cannot be applied unambiguously."""


def apply_edit(content: str, old_string: str, new_string: str) -> str:
    """Replace the single occurrence of ``old_string`` in ``content``.

    Raises :class:`EditError` when ``old_string`` is empty, absent, or appears
    more than once — the model must then provide more context to disambiguate.
    """
    if old_string == "":
        raise EditError("old_string must be non-empty.")
    count = content.count(old_string)
    if count == 0:
        raise EditError(
            "old_string was not found in the file. The text must match exactly, "
            "including whitespace and indentation."
        )
    if count > 1:
        raise EditError(
            f"old_string is ambiguous: found {count} occurrences. "
            "Include more surrounding lines to make it unique."
        )
    return content.replace(old_string, new_string, 1)


def format_read_output(content: str) -> str:
    """Prefix each line with a 1-based line number for the ``read`` tool."""
    if content == "":
        return "(empty file)"
    return "\n".join(
        f"{i:>6}\t{line}" for i, line in enumerate(content.splitlines(), 1)
    )

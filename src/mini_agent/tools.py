"""Built-in tool handlers, registry, and dispatch.

The stable public imports remain in this module.  Reusable schemas, value
types, output helpers, file-editing primitives, and network policy live in
``mini_agent.tooling`` so this file can focus on runtime orchestration.
"""

import copy
import inspect
import json
import subprocess
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from mini_agent.config import Config, get_default_config
from mini_agent.evidence import EventFact, extract_event_evidence
from mini_agent.exceptions import AgentExit, MaxTime, Submitted
from mini_agent.tooling.files import EditError, apply_edit, format_read_output
from mini_agent.tooling.network import (
    _network_command_in_segment,
    find_network_command,
)
from mini_agent.tooling.output import (
    DEFAULT_MAX_CHARS,
    _truncate_by_chars,
    decode_timeout_output,
    format_execution_observation,
    truncate_output,
)
from mini_agent.tooling.schemas import (
    BASH_SCHEMA,
    EDIT_SCHEMA,
    READ_SCHEMA,
    SUBMIT_SCHEMA,
    TRAJECTORY_SCHEMA,
    WRITE_SCHEMA,
)
from mini_agent.tooling.types import ToolContext, ToolDefinition, ToolHandler, ToolResult


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _remaining_time(context: ToolContext) -> float | None:
    if context.deadline is None:
        return None
    remaining = context.deadline - time.monotonic()
    if remaining <= 0:
        raise MaxTime()
    return remaining


def _environment_call(method, *args, context: ToolContext):
    """Call an environment method with the remaining deadline when supported."""

    remaining = _remaining_time(context)
    if remaining is not None:
        try:
            accepts_timeout = "timeout" in inspect.signature(method).parameters
        except (TypeError, ValueError):
            accepts_timeout = False
        if accepts_timeout:
            return method(*args, timeout=remaining)
    return method(*args)


def _handle_submit(args: dict[str, Any], context: ToolContext) -> ToolResult:
    if "output" not in args:
        return ToolResult("Error: 'submit' requires an 'output' argument.")
    submission = args["output"]
    if not isinstance(submission, str):
        return ToolResult("Error: 'submit' 'output' must be a string.")
    print("Submit:", submission)
    return ToolResult("Submitted.", submission=submission)


def _handle_bash(args: dict[str, Any], context: ToolContext) -> ToolResult:
    command = args.get("command")
    if not command:
        return ToolResult("Error: 'bash' requires a 'command' argument.")

    if context.config.environment.block_network_commands:
        blocked = find_network_command(command)
        if blocked is not None:
            output = (
                "Error: command blocked by policy because external network "
                f"access is disabled for this run: {blocked}"
            )
            print("Action:", command)
            print("Output:", output)
            return ToolResult(output)

    defaults = context.config.tools
    max_lines = args.get("lines", defaults.default_max_lines)
    max_chars = getattr(defaults, "default_max_chars", DEFAULT_MAX_CHARS)
    timeout = args.get("timeout", defaults.default_timeout)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        return ToolResult("Error: 'bash' 'timeout' must be an integer >= 1.")
    remaining = _remaining_time(context)
    if remaining is not None:
        timeout = min(timeout, remaining)
    print("Action:", command)

    needs_final_truncation = False
    try:
        raw = context.environment.execute(command, timeout=timeout)
        output = format_execution_observation(
            raw, max_lines, max_chars=max_chars
        )
    except subprocess.TimeoutExpired as exc:
        needs_final_truncation = True
        partial = decode_timeout_output(exc)
        output = (
            f"{truncate_output(partial, max_lines, max_chars=max_chars)}\n"
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
        needs_final_truncation = True
        output = f"Error: {exc}"

    # Timeout guidance and exception text are appended outside the structured
    # formatter above, so enforce the same final budget on every branch.
    if needs_final_truncation:
        output = truncate_output(output, max_lines, max_chars=max_chars)
    print("Output:", output)
    return ToolResult(output)


def _handle_read(args: dict[str, Any], context: ToolContext) -> ToolResult:
    path = args.get("path")
    if not path:
        return ToolResult("Error: 'read' requires a 'path' argument.")

    print("Read:", path)
    line_start = args.get("line_start", 1)
    max_lines = args.get("lines", context.config.tools.default_max_lines)
    if (
        isinstance(line_start, bool)
        or not isinstance(line_start, int)
        or line_start < 1
    ):
        return ToolResult("Error: 'read' 'line_start' must be an integer >= 1.")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines < 1:
        return ToolResult("Error: 'read' 'lines' must be an integer >= 1.")
    max_chars = getattr(
        context.config.tools, "default_max_chars", DEFAULT_MAX_CHARS
    )
    try:
        content = _environment_call(context.environment.read_file, path, context=context)
    except AgentExit:
        raise
    except FileNotFoundError:
        output = f"Error: file not found: {path}"
    except IsADirectoryError:
        output = f"Error: {path} is a directory, not a file."
    except Exception as exc:
        output = f"Error: {exc}"
    else:
        output = format_read_output(
            content,
            start_line=line_start,
            max_lines=max_lines,
            max_chars=max_chars,
        )
    # Error strings (including a model-supplied path) must obey the same bound
    # as successful file reads.
    output = _truncate_by_chars(output, max_chars)
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
        original = _environment_call(context.environment.read_file, path, context=context)
    except AgentExit:
        raise
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
                _environment_call(
                    context.environment.write_file,
                    path,
                    updated,
                    context=context,
                )
            except AgentExit:
                raise
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
        _environment_call(
            context.environment.write_file,
            path,
            content,
            context=context,
        )
    except AgentExit:
        raise
    except Exception as exc:
        output = f"Error: {exc}"
    else:
        output = f"Wrote {path} ({len(content)} characters)."
    print("Output:", output)
    return ToolResult(output)


def _handle_trajectory(args: dict[str, Any], context: ToolContext) -> ToolResult:
    """Return bounded, read-only evidence from the current run's event journal."""
    if context.event_log is None:
        return ToolResult("Error: trajectory history is unavailable in this context.")

    query = args.get("query")
    start = args.get("start", 0)
    limit = args.get("events", 20)
    event_type = args.get("event_type")
    role = args.get("role")
    tool_name = args.get("tool_name")
    returncode = args.get("returncode", None)
    if query is not None and not isinstance(query, str):
        return ToolResult("Error: 'trajectory' 'query' must be a string.")
    for name, value in (
        ("event_type", event_type),
        ("role", role),
        ("tool_name", tool_name),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return ToolResult(
                f"Error: 'trajectory' '{name}' must be a non-empty string."
            )
    if returncode is not None and (
        isinstance(returncode, bool) or not isinstance(returncode, int)
    ):
        return ToolResult("Error: 'trajectory' 'returncode' must be an integer.")
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        return ToolResult("Error: 'trajectory' 'start' must be an integer >= 0.")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > 50
    ):
        return ToolResult(
            "Error: 'trajectory' 'events' must be an integer from 1 to 50."
        )

    needle = query.casefold() if query else None
    normalized = extract_event_evidence(context.event_log)
    facts_by_sequence: dict[str, list[EventFact]] = defaultdict(list)
    for fact in normalized.facts:
        facts_by_sequence[fact.sequence].append(fact)
    matches: list[tuple[int, str]] = []
    for index, event in enumerate(context.event_log):
        sequence = event.get("sequence", index)
        if not isinstance(sequence, int) or sequence < start:
            continue
        serialized = json.dumps(event, ensure_ascii=False, default=repr)
        if needle is not None and needle not in serialized.casefold():
            continue
        facts = facts_by_sequence.get(str(sequence), [])
        message = event.get("message")
        source = message if isinstance(message, Mapping) else event
        raw_event_type = str(event.get("type", ""))
        raw_role = str(source.get("role", ""))
        if event_type is not None and raw_event_type.casefold() != event_type.casefold():
            continue
        if role is not None and raw_role.casefold() != role.casefold():
            continue
        if tool_name is not None and not any(
            fact.tool_name.casefold() == tool_name.casefold() for fact in facts
        ):
            continue
        if returncode is not None and not any(
            fact.has_returncode and fact.returncode == returncode for fact in facts
        ):
            continue
        matches.append((sequence, serialized))

    selected = matches[:limit]
    if not selected:
        scope = f" matching {query!r}" if query else ""
        return ToolResult(f"No trajectory events{scope} at or after sequence {start}.")

    lines = [f"event {sequence}: {serialized}" for sequence, serialized in selected]
    if len(matches) > len(selected):
        next_start = selected[-1][0] + 1
        lines.append(
            f"[... {len(matches) - len(selected)} later matching events not shown; "
            f"continue with start={next_start} ...]"
        )
    output = "\n".join(lines)
    max_chars = getattr(
        context.config.tools, "default_max_chars", DEFAULT_MAX_CHARS
    )
    return ToolResult(_truncate_by_chars(output, max_chars))


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
    ToolDefinition("trajectory", TRAJECTORY_SCHEMA, _handle_trajectory),
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
        schema = definition.schema
        if name == "bash" and cfg.environment.block_network_commands:
            schema = copy.deepcopy(schema)
            schema["function"]["description"] = (
                "Execute a bash command in the prepared offline environment. "
                "External downloads, remote Git operations, and package-manager "
                "network commands are blocked; use only repository files and "
                "already-installed dependencies. "
                + schema["function"]["description"].replace(
                    "e.g. pip install or git clone.",
                    "e.g. long local builds or test suites.",
                ).replace(
                    "slow commands like pip install, git clone, or long builds.",
                    "slow local commands such as builds or test suites.",
                )
            )
            schema["function"]["parameters"]["properties"]["timeout"]["description"] = (
                "Maximum seconds to wait for the local command (default 30). "
                "Set higher for slow builds or test suites; external downloads "
                "and remote package installation are blocked in this run."
            )
        schemas.append(schema)
    return schemas


def execute_tool_call(
    tc,
    messages: list[dict],
    environment,
    config: Config | None = None,
    *,
    defer_submission: bool = False,
    event_log: Sequence[dict[str, Any]] | None = None,
    deadline: float | None = None,
) -> str | None:
    """Validate, dispatch, and record one OpenAI tool call.

    Parameters
    ----------
    tc:
        An OpenAI ``ToolCall`` object with ``.id``, ``.function.name``,
        and ``.function.arguments``.
    messages:
        The message history (mutated in place with tool results).
    environment:
        An execution environment with ``.execute(command, timeout)`` returning
        either a structured execution mapping or a legacy string.
    config:
        Optional :class:`Config`. Its ``tools.enabled`` list controls both
        model visibility and execution permission.
    defer_submission:
        Capture a valid submission as a draft instead of ending the run. The
        draft is returned to the caller and recorded accurately in history.
    event_log:
        Optional append-only run journal exposed only to enabled read-only
        introspection tools. Ordinary environment tools do not use it.
    deadline:
        Optional monotonic run deadline. Built-in tools cap blocking operations
        to its remaining time and stop before later side effects once expired.

    Raises
    ------
    Submitted
        When the tool is ``submit`` (the run completed with an answer).
    """
    cfg = config or get_default_config()
    function = getattr(tc, "function", None)
    name = getattr(function, "name", "<unknown>")
    if not isinstance(name, str) or not name:
        name = "<unknown>"
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
            args = json.loads(getattr(function, "arguments", None))
            if not isinstance(args, dict):
                raise ValueError("arguments must decode to a JSON object")
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            result = ToolResult(f"Error: invalid arguments for '{name}': {exc}")
        else:
            try:
                result = definition.handler(
                    args,
                    ToolContext(
                        environment,
                        cfg,
                        event_log=event_log,
                        deadline=deadline,
                    ),
                )
            except AgentExit as exc:
                append_tool_result(
                    tc,
                    messages,
                    f"Tool stopped because run limit '{exc.exit_status}' was reached.",
                )
                raise
            except Exception as exc:
                result = ToolResult(f"Error: {exc}")

    if result.submission is not None:
        if defer_submission:
            append_tool_result(
                tc,
                messages,
                "Draft submission captured; required review is still pending.",
            )
            return result.submission
        append_tool_result(tc, messages, result.content)
        raise Submitted(result.submission)
    append_tool_result(tc, messages, result.content)
    return None


def append_tool_result(tc, messages: list[dict], content: str) -> None:
    """Append a protocol-compliant response for one assistant tool call.

    Keeping this small operation centralized makes it possible for the agent
    loop to acknowledge calls it intentionally skips after ``submit`` (or
    after a run limit is reached) without dispatching their handlers.
    """
    tool_call_id = getattr(tc, "id", "")
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    })


def append_skipped_tool_result(
    tc, messages: list[dict], *, reason: str = "submit was already requested"
) -> None:
    """Record a deterministic result for a tool call that was not executed."""
    function = getattr(tc, "function", None)
    name = getattr(function, "name", "<unknown>")
    if not isinstance(name, str) or not name:
        name = "<unknown>"
    append_tool_result(
        tc,
        messages,
        f"Skipped tool '{name}': {reason}; no tool action was executed.",
    )


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
# Pure formatting, file, and network helpers are imported above from
# ``mini_agent.tooling``.  Their names stay in this module for compatibility
# and so handlers remain patchable through the historical module path.

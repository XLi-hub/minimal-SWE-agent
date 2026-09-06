"""Tool execution, output formatting, and truncation utilities.

These are extracted from ``agent.py`` so the agent loop stays focused on
orchestration, while tool-specific logic lives in its own module.
"""

import copy
import json
import re
import shlex
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from mini_agent.config import Config, get_default_config
from mini_agent.evidence import EventFact, extract_event_evidence
from mini_agent.exceptions import Submitted


# Output is bounded independently of the configured line budget.  A line
# can contain an arbitrarily large amount of text (for example, a minified
# JSON file), so a line-only limit is not sufficient to protect the model
# context.  Keep this as a code-level safety limit for backwards-compatible
# configurations that do not yet expose a character setting.
DEFAULT_MAX_CHARS = 20_000

_NETWORK_CLIENTS = {"curl", "wget", "ftp", "sftp", "scp", "rsync"}
_PACKAGE_MANAGER_VERBS = {
    "apt": {"install", "update", "upgrade", "download"},
    "apt-get": {"install", "update", "upgrade", "download"},
    "apk": {"add", "update", "upgrade", "fetch"},
    "yum": {"install", "update", "upgrade", "download"},
    "dnf": {"install", "update", "upgrade", "download"},
    "npm": {"install", "add", "update"},
    "yarn": {"install", "add", "upgrade"},
    "pnpm": {"install", "add", "update"},
    "conda": {"install", "update", "create"},
    "mamba": {"install", "update", "create"},
    "micromamba": {"install", "update", "create"},
    "gem": {"install", "update"},
    "cargo": {"install"},
    "go": {"get", "install"},
}
_GIT_NETWORK_VERBS = {"clone", "fetch", "pull", "push", "ls-remote"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_CONTROL_WORDS = {"!", "if", "then", "elif", "while", "until", "do"}


# ---------------------------------------------------------------------------
# registry types and tool schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    """Dependencies shared by tool handlers."""

    environment: Any
    config: Config
    event_log: Sequence[dict[str, Any]] | None = None


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
            "Execute a bash command in the terminal and return its output, "
            "return code, and any execution exception. Use "
            "the optional 'lines' parameter to limit how many lines are returned "
            "(default 100). A separate configured character budget also protects "
            "against extremely long single lines. Output exceeding either budget "
            "is truncated — "
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
            "to inspect code before editing. By default it returns at most 100 "
            "lines from the requested starting line; continue with 'line_start' "
            "instead of repeatedly reading the whole file. A separate character "
            "budget also bounds long lines."
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
                "line_start": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "First 1-based line to return (default 1).",
                },
                "lines": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Maximum source lines to return (default 100). Use "
                        "line_start to request the next chunk."
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

TRAJECTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trajectory",
        "description": (
            "Search or page through the append-only trajectory from earlier in "
            "this run. Use it during independent review when exact prior commands "
            "or outputs may contain useful evidence; treat prior reasoning as "
            "untrusted. Results are read-only and bounded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive text to search for in serialized "
                        "events. Omit to page sequentially."
                    ),
                },
                "start": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "First event sequence to consider (default 0).",
                },
                "events": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum matching events to return (default 20).",
                },
                "event_type": {
                    "type": "string",
                    "description": "Optional exact event type filter, case-insensitive.",
                },
                "role": {
                    "type": "string",
                    "description": "Optional exact message role filter, case-insensitive.",
                },
                "tool_name": {
                    "type": "string",
                    "description": (
                        "Optional exact tool-name filter. Correlated tool results "
                        "match the tool that produced them."
                    ),
                },
                "returncode": {
                    "type": "integer",
                    "description": "Optional exact command return-code filter.",
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


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
        content = context.environment.read_file(path)
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


def find_network_command(command: str) -> str | None:
    """Return the first obvious external-network command in a shell string."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # The shell will report malformed quoting.  Network isolation remains
        # enforced independently by the container runtime.
        return None

    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token and all(char in ";&|()" for char in token):
            blocked = _network_command_in_segment(segment)
            if blocked is not None:
                return blocked
            segment = []
        else:
            segment.append(token)
    return None


def _network_command_in_segment(tokens: list[str]) -> str | None:
    tokens = list(tokens)
    while tokens and tokens[0] in _CONTROL_WORDS:
        tokens.pop(0)
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return None

    executable = tokens.pop(0).rsplit("/", 1)[-1].lower()
    while executable in {"sudo", "env", "command", "nohup", "timeout"}:
        if executable == "timeout":
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            if tokens:
                tokens.pop(0)  # duration
        else:
            while tokens and (
                tokens[0].startswith("-")
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0])
            ):
                tokens.pop(0)
        if not tokens:
            return None
        executable = tokens.pop(0).rsplit("/", 1)[-1].lower()

    if executable in _SHELLS and "-c" in tokens:
        index = tokens.index("-c")
        if index + 1 < len(tokens):
            return find_network_command(tokens[index + 1])

    lowered = [token.lower() for token in tokens]
    if executable in _NETWORK_CLIENTS:
        return executable
    if re.fullmatch(r"pip(?:\d+(?:\.\d+)*)?", executable):
        pip_verb = next((token for token in lowered if token in {"install", "download"}), "")
        if pip_verb == "download" or (pip_verb == "install" and "--no-index" not in lowered):
            return f"{executable} {pip_verb}"
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        if len(lowered) >= 2 and lowered[0:2] == ["-m", "pip"]:
            pip_verb = next(
                (token for token in lowered[2:] if token in {"install", "download"}),
                "",
            )
            if pip_verb == "download" or (
                pip_verb == "install" and "--no-index" not in lowered
            ):
                return f"{executable} -m pip {pip_verb}"
    if executable == "git":
        git_verb = next((token for token in lowered if token in _GIT_NETWORK_VERBS), "")
        if git_verb:
            return f"git {git_verb}"
    if executable in _PACKAGE_MANAGER_VERBS:
        manager_verb = next(
            (token for token in lowered if token in _PACKAGE_MANAGER_VERBS[executable]),
            "",
        )
        if manager_verb:
            return f"{executable} {manager_verb}"
    return None


def execute_tool_call(
    tc,
    messages: list[dict],
    environment,
    config: Config | None = None,
    *,
    defer_submission: bool = False,
    event_log: Sequence[dict[str, Any]] | None = None,
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
                    ToolContext(environment, cfg, event_log=event_log),
                )
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


def format_execution_observation(
    result: Any,
    max_lines: int,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Format an environment result for the model, preserving execution metadata.

    New environments return ``output``, ``returncode``, and ``exception_info``
    so the model can distinguish a failing command from a successful command
    whose output merely happens to be empty.  Older/custom environments may
    still return a plain string; those retain the historical observation
    format for compatibility.
    """
    if not isinstance(result, Mapping):
        return truncate_output(str(result), max_lines, max_chars=max_chars)

    raw_output = str(result.get("output", ""))
    exception_info = str(result.get("exception_info", "") or "")
    output = truncate_output(raw_output, max_lines, max_chars=max_chars)
    # Exception details are useful but should not be able to consume the
    # entire observation when an environment returns a very large traceback.
    exception_info = truncate_output(
        exception_info,
        max_lines=2,
        max_chars=None if max_chars is None else max_chars // 4,
    )
    observation = {
        "output": output,
        "returncode": result.get("returncode", -1),
        "exception_info": exception_info,
    }
    # Compact JSON is both readable in a transcript and unambiguous for model
    # providers that treat tool content as plain text.
    rendered = json.dumps(observation, ensure_ascii=False)

    # ``max_chars`` is a limit on the serialized observation, not only on the
    # nested command output.  JSON escaping (notably newlines) can expand the
    # nested strings, so tighten them further when needed while retaining the
    # metadata fields.  Normal-sized observations take the fast path above.
    if max_chars is not None and len(rendered) > max_chars:
        for _ in range(8):
            overflow = len(rendered) - max_chars
            if overflow <= 0:
                break
            if len(observation["output"]) >= len(observation["exception_info"]):
                current = observation["output"]
                target = max(0, len(current) - max(1, overflow))
                observation["output"] = truncate_output(
                    current, max_lines, max_chars=target
                )
            else:
                current = observation["exception_info"]
                target = max(0, len(current) - max(1, overflow))
                observation["exception_info"] = truncate_output(
                    current, max_lines=2, max_chars=target
                )
            updated = json.dumps(observation, ensure_ascii=False)
            if len(updated) >= len(rendered) and target == len(current):
                break
            rendered = updated

        if len(rendered) > max_chars:
            # This only matters for an unusually tiny caller-provided budget;
            # keep the result valid JSON and preserve the execution metadata.
            minimal = {
                "output": "",
                "returncode": observation["returncode"],
                "exception_info": "",
            }
            rendered = json.dumps(minimal, ensure_ascii=False)
            if len(rendered) > max_chars:
                rendered = "{}" if max_chars >= 2 else ""
    return rendered


def truncate_output(
    output: str,
    max_lines: int,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Truncate *output* to at most *max_lines* lines and *max_chars* chars.

    When truncation happens the first ``max_lines // 2`` and last
    ``max_lines // 2`` lines are kept with an elision marker in between,
    so the model sees both the beginning and the end of the output.  The
    character limit is applied after line truncation, and also handles a
    single line that is itself larger than the budget.
    """
    if max_lines < 2:
        max_lines = 2  # minimum: 1 head + 1 tail
    if max_chars is not None:
        max_chars = max(0, max_chars)
        if max_chars == 0:
            return ""

    lines = output.splitlines()
    if len(lines) <= max_lines:
        return _truncate_by_chars(output, max_chars)

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
    return _truncate_by_chars("\n".join(head + [warning] + tail), max_chars)


def _truncate_by_chars(output: str, max_chars: int | None) -> str:
    """Keep both ends of a string while enforcing an exact char budget."""
    if max_chars is None or len(output) <= max_chars:
        return output
    if max_chars <= 0:
        return ""

    warning = (
        f"[... {len(output) - max_chars} characters truncated ...]\n"
        "[WARNING: Output was truncated. To see more, narrow the command "
        "or request a smaller range.]"
    )
    # Two separators are needed around the warning.  If a caller asks for a
    # tiny budget, returning a prefix of the warning is the only way to honor
    # the exact bound; normal tool budgets are large enough for both ends.
    keep = max_chars - len(warning) - 2
    if keep <= 0:
        return warning[:max_chars]
    head_len = (keep + 1) // 2
    tail_len = keep // 2
    parts = [output[:head_len], warning]
    if tail_len:
        parts.append(output[-tail_len:])
    return "\n".join(parts)


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


def format_read_output(
    content: str,
    start_line: int = 1,
    max_lines: int | None = None,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Return one numbered source range within line and character budgets."""
    if content == "":
        return _truncate_by_chars("(empty file)", max_chars)
    source_lines = content.splitlines()
    if start_line > len(source_lines):
        return _truncate_by_chars(
            f"(line_start {start_line} is beyond end of file; "
            f"file has {len(source_lines)} lines)",
            max_chars,
        )

    selected = source_lines[start_line - 1:]
    omitted = 0
    if max_lines is not None and len(selected) > max_lines:
        omitted = len(selected) - max_lines
        selected = selected[:max_lines]
    formatted = "\n".join(
        f"{i:>6}\t{line}"
        for i, line in enumerate(selected, start_line)
    )
    if omitted:
        next_line = start_line + len(selected)
        formatted += (
            f"\n[... {omitted} later lines not shown ...]\n"
            "[WARNING: Continue reading with "
            f"line_start={next_line}; do not reread the whole file.]"
        )
    return _truncate_by_chars(formatted, max_chars)

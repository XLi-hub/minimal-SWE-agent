"""Tool execution, output formatting, and truncation utilities.

These are extracted from ``agent.py`` so the agent loop stays focused on
orchestration, while tool-specific logic lives in its own module.
"""

import json
import subprocess

from src.mini_agent.config import Config, get_default_config
from src.mini_agent.exceptions import Submitted


# ---------------------------------------------------------------------------
# tool dispatch
# ---------------------------------------------------------------------------


def execute_tool_call(tc, messages: list[dict], environment,
                      config: Config | None = None) -> None:
    """Execute a single tool call and update *messages* in place.

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
        Optional :class:`Config` — supplies ``default_max_lines`` /
        ``default_timeout`` when the model omits them.

    Raises
    ------
    Submitted
        When the tool is ``submit`` (the run completed with an answer).
    """
    name = tc.function.name
    args = json.loads(tc.function.arguments)
    tool_defaults = (config or get_default_config()).tools

    if name == "submit":
        submission = args.get("output", "")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": "Submitted.",
        })
        print("Submit:", submission)
        raise Submitted(submission)

    if name == "bash":
        command = args["command"]
        max_lines = args.get("lines", tool_defaults.default_max_lines)
        timeout = args.get("timeout", tool_defaults.default_timeout)
        print("Action:", command)

        try:
            raw = environment.execute(command, timeout=timeout)
            output = truncate_output(raw, max_lines)
        except subprocess.TimeoutExpired as e:
            partial = decode_timeout_output(e)
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
        except Exception as e:
            output = f"Error: {e}"

        print("Output:", output)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        })
        return None

    if name == "read":
        path = args.get("path")
        if not path:
            output = "Error: 'read' requires a 'path' argument."
        else:
            print("Read:", path)
            try:
                content = environment.read_file(path)
            except FileNotFoundError:
                output = f"Error: file not found: {path}"
            except IsADirectoryError:
                output = f"Error: {path} is a directory, not a file."
            except Exception as e:
                output = f"Error: {e}"
            else:
                output = format_read_output(content)
            print("Output:", output)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        })
        return None

    if name == "edit":
        path = args.get("path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not path or old_string is None or new_string is None:
            output = "Error: 'edit' requires 'path', 'old_string', and 'new_string'."
        else:
            print("Edit:", path)
            try:
                original = environment.read_file(path)
            except FileNotFoundError:
                output = f"Error: file not found: {path}"
            except Exception as e:
                output = f"Error: {e}"
            else:
                try:
                    updated = apply_edit(original, old_string, new_string)
                except EditError as e:
                    output = f"Error: {e}"
                else:
                    try:
                        environment.write_file(path, updated)
                    except Exception as e:
                        output = f"Error: {e}"
                    else:
                        output = (
                            f"Edited {path}: replaced the unique occurrence of:\n"
                            f"--- old ---\n{old_string}\n--- new ---\n{new_string}"
                        )
            print("Output:", output)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        })
        return None

    if name == "write":
        path = args.get("path")
        content = args.get("content")
        if not path or content is None:
            output = "Error: 'write' requires 'path' and 'content'."
        else:
            print("Write:", path)
            try:
                environment.write_file(path, content)
            except Exception as e:
                output = f"Error: {e}"
            else:
                output = f"Wrote {path} ({len(content)} characters)."
            print("Output:", output)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": output,
        })
        return None

    # Unknown tool — tell the model so it can self-correct.
    available = ", ".join(tool_defaults.tool_names())
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": f"Error: unknown tool '{name}'. Available tools: {available}.",
    })
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

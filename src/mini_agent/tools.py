"""Tool execution, output formatting, and truncation utilities.

These are extracted from ``agent.py`` so the agent loop stays focused on
orchestration, while tool-specific logic lives in its own module.
"""

import json
import subprocess

from src.mini_agent.config import DEFAULT_MAX_LINES, DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# tool dispatch
# ---------------------------------------------------------------------------


def execute_tool_call(tc, messages: list[dict], result: dict, environment) -> bool:
    """Execute a single tool call and update *messages* in place.

    Parameters
    ----------
    tc:
        An OpenAI ``ToolCall`` object with ``.id``, ``.function.name``,
        and ``.function.arguments``.
    messages:
        The message history (mutated in place with tool results).
    result:
        The run result dict (mutated in place for ``submit``).
    environment:
        An execution environment with ``.execute(command, timeout) -> str``.

    Returns
    -------
    bool
        ``True`` when the tool signals the agent loop should exit
        (e.g. ``submit``), ``False`` otherwise.
    """
    name = tc.function.name
    args = json.loads(tc.function.arguments)

    if name == "submit":
        submission = args.get("output", "")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": "Submitted.",
        })
        print("Submit:", submission)
        result["exit_status"] = "submitted"
        result["submission"] = submission
        return True

    if name == "bash":
        command = args["command"]
        max_lines = args.get("lines", DEFAULT_MAX_LINES)
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
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
        return False

    # Unknown tool — tell the model so it can self-correct.
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": f"Error: unknown tool '{name}'. Available tools: bash, submit.",
    })
    return False


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

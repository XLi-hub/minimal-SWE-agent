"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import json
import subprocess
import time

from src.mini_agent.config import (
    BASH_TOOL,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TIME,
    DEFAULT_TIMEOUT,
    INSTANCE_TEMPLATE,
    SUBMIT_TOOL,
    SYSTEM_PROMPT,
)


class Agent:
    """AI Agent：循环查询 → 工具调用 → 执行，直到完成用户任务。

    model 需提供 .query(messages, tools=None) → OpenAI response.
    environment 需提供 .execute(command, timeout=30) → str.
    """

    def __init__(self, model, environment):
        self.model = model
        self.environment = environment

    def run(self, task: str, max_steps: int = DEFAULT_MAX_STEPS,
            max_time: float | None = DEFAULT_MAX_TIME) -> dict:
        """Run the agent loop for a given user task.

        Parameters
        ----------
        task:
            The user's task description.
        max_steps:
            Maximum tool-calling iterations before the agent stops
            (default: *DEFAULT_MAX_STEPS* = 250).  Each ``model.query()``
            call counts as one step, regardless of how many tool
            calls the model makes in that step.
        max_time:
            Maximum wall-clock time in seconds for the entire run
            (default: *DEFAULT_MAX_TIME* = 1800).  Pass ``None`` to
            disable the time limit.

        Returns
        -------
        dict
            With keys:

            - ``exit_status``: one of ``"submitted"``, ``"no_tool_calls"``,
              ``"max_steps"``, ``"max_time"``, ``"interrupted"``, ``"error"``
            - ``submission``: the final answer (empty if not submitted)
            - ``messages``: the full message history
        """
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": INSTANCE_TEMPLATE.format(task=task)},
        ]

        result: dict = {"exit_status": "error", "submission": "", "messages": messages}
        deadline = time.monotonic() + max_time if max_time is not None else None

        for _ in range(max_steps):
            if deadline is not None and time.monotonic() > deadline:
                result["exit_status"] = "max_time"
                return result
            try:
                response = self.model.query(
                    messages, tools=[BASH_TOOL, SUBMIT_TOOL]
                )
                choice = response.choices[0]
                msg = choice.message

                # No tool calls → treat as exit (legacy / fallback)
                if not msg.tool_calls:
                    messages.append(
                        {"role": "assistant", "content": msg.content}
                    )
                    print("LM output:", msg.content)
                    result["exit_status"] = "no_tool_calls"
                    return result

                print("LM output:", msg.content)

                messages.append(_format_assistant_message(msg))

                for tc in msg.tool_calls:
                    if self._handle_tool_call(tc, messages, result):
                        return result

            except KeyboardInterrupt:
                result["exit_status"] = "interrupted"
                return result
            except Exception as e:
                messages.append(
                    {"role": "user", "content": f"Error: {e}"}
                )

        # Ran out of steps — return partial progress.
        result["exit_status"] = "max_steps"
        return result

    # ------------------------------------------------------------------
    # tool dispatch
    # ------------------------------------------------------------------

    def _handle_tool_call(self, tc, messages: list[dict], result: dict) -> bool:
        """Execute a single tool call and update *messages* in place.

        Returns ``True`` when the tool signals the agent loop should exit
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
                raw = self.environment.execute(command, timeout=timeout)
                output = _truncate_output(raw, max_lines)
            except subprocess.TimeoutExpired as e:
                partial = _decode_timeout_output(e)
                output = (
                    f"{_truncate_output(partial, max_lines)}\n"
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


def _format_assistant_message(msg) -> dict:
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


def _decode_timeout_output(exc: subprocess.TimeoutExpired) -> str:
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


def _truncate_output(output: str, max_lines: int) -> str:
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


# 向后兼容：延迟创建，避免 import 时就需要 API key
_default_agent: Agent | None = None


def run(task: str, max_steps: int = DEFAULT_MAX_STEPS,
        max_time: float | None = DEFAULT_MAX_TIME) -> dict:
    global _default_agent
    if _default_agent is None:
        from src.mini_agent.model import Model            # noqa: E402
        from src.mini_agent.environments.local import LocalEnvironment  # noqa: E402
        _default_agent = Agent(Model(), LocalEnvironment())
    return _default_agent.run(task, max_steps=max_steps, max_time=max_time)

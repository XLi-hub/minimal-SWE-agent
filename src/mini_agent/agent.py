"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import json

from src.mini_agent.config import (
    BASH_TOOL,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_STEPS,
    DEFAULT_TIMEOUT,
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

    def run(self, task: str, max_steps: int = DEFAULT_MAX_STEPS) -> dict:
        """Run the agent loop for a given user task.

        Parameters
        ----------
        task:
            The user's task description.
        max_steps:
            Maximum tool-calling iterations before the agent stops
            (default: *DEFAULT_MAX_STEPS* = 50).  Each ``model.query()``
            call counts as one step, regardless of how many tool
            calls the model makes in that step.

        Returns
        -------
        dict
            With keys:

            - ``exit_status``: one of ``"submitted"``, ``"no_tool_calls"``,
              ``"max_steps"``, ``"interrupted"``, ``"error"``
            - ``submission``: the final answer (empty if not submitted)
            - ``messages``: the full message history
        """
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        result: dict = {"exit_status": "error", "submission": "", "messages": messages}

        for _ in range(max_steps):
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
                        return result

                    if name != "bash":
                        continue

                    command = args["command"]
                    max_lines = args.get("lines", DEFAULT_MAX_LINES)
                    timeout = args.get("timeout", DEFAULT_TIMEOUT)
                    print("Action:", command)

                    try:
                        raw = self.environment.execute(command, timeout=timeout)
                        output = _truncate_output(raw, max_lines)
                    except Exception as e:
                        output = f"Error: {e}"

                    print("Output:", output)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output,
                        }
                    )

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

    return "\n".join(
        head
        + [f"[... {elided} lines truncated ({len(lines)} total, {max_lines} shown) ...]"]
        + tail
    )


# 向后兼容：延迟创建，避免 import 时就需要 API key
_default_agent: Agent | None = None


def run(task: str, max_steps: int = DEFAULT_MAX_STEPS) -> dict:
    global _default_agent
    if _default_agent is None:
        from src.mini_agent.model import Model            # noqa: E402
        from src.mini_agent.environments.local import LocalEnvironment  # noqa: E402
        _default_agent = Agent(Model(), LocalEnvironment())
    return _default_agent.run(task, max_steps=max_steps)

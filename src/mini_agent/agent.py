"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import time

from src.mini_agent.config import (
    BASH_TOOL,
    DEFAULT_MAX_STEPS,
    DEFAULT_MAX_TIME,
    INSTANCE_TEMPLATE,
    SUBMIT_TOOL,
    SYSTEM_PROMPT,
)
from src.mini_agent.tools import execute_tool_call, format_assistant_message


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

                messages.append(format_assistant_message(msg))

                for tc in msg.tool_calls:
                    if execute_tool_call(tc, messages, result, self.environment):
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

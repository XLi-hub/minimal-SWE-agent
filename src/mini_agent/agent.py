"""Agent 主循环 — 使用模型 tool calling 替代文本解析."""

import json

from src.mini_agent.config import BASH_TOOL, DEFAULT_MAX_LINES, SYSTEM_PROMPT


class Agent:
    """AI Agent：循环查询 → 工具调用 → 执行，直到完成用户任务。

    model 需提供 .query(messages, tools=None) → OpenAI response.
    environment 需提供 .execute(command, timeout=30) → str.
    """

    def __init__(self, model, environment):
        self.model = model
        self.environment = environment

    def run(self, task: str) -> list[dict]:
        """Run the agent loop for a given user task.

        Returns the full message history (includes tool-call and
        tool-result messages).
        """
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        while True:
            try:
                # 1. 查询 LM（带工具定义）
                response = self.model.query(messages, tools=[BASH_TOOL])
                choice = response.choices[0]
                msg = choice.message

                # 2. 没有工具调用 → 任务完成
                if not msg.tool_calls:
                    messages.append(
                        {"role": "assistant", "content": msg.content}
                    )
                    print("LM output:", msg.content)
                    break

                print("LM output:", msg.content)

                # 3. 记录 assistant 消息（含 tool_calls）
                messages.append(_format_assistant_message(msg))

                # 4. 执行每一个工具调用
                for tc in msg.tool_calls:
                    if tc.function.name != "bash":
                        continue

                    args = json.loads(tc.function.arguments)
                    command = args["command"]
                    max_lines = args.get("lines", DEFAULT_MAX_LINES)
                    print("Action:", command)

                    try:
                        raw = self.environment.execute(command)
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
                break
            except Exception as e:
                # 把异常变成 user message，让循环继续
                messages.append(
                    {"role": "user", "content": f"Error: {e}"}
                )

        return messages


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


def run(task: str) -> list[dict]:
    global _default_agent
    if _default_agent is None:
        from src.mini_agent.model import Model            # noqa: E402
        from src.mini_agent.environment import Environment  # noqa: E402
        _default_agent = Agent(Model(), Environment())
    return _default_agent.run(task)

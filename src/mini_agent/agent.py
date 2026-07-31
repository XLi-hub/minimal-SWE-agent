"""Agent 主循环 — 把模型、解析、执行串起来."""

from src.mini_agent.parser import parse_action


SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "When you want to run a command, wrap it in ```bash-action\n<command>\n```. "
    "To finish, run the exit command."
)


class Agent:
    """AI Agent：循环查询 → 解析 → 执行，直到完成用户任务。

    model 和 environment 从外部注入，可以自由替换
    （比如换用 OpenAI、Docker 环境），不用改 Agent 本身。
    """

    def __init__(self, model, environment):
        """model 需提供 .query(messages) → str
           environment 需提供 .execute(command, timeout) → str
        """
        self.model = model
        self.environment = environment

    def run(self, task: str) -> list[dict[str, str]]:
        """Run the agent loop for a given user task.

        Returns the full message history.
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        while True:
            try:
                # 1. 查询 LM
                lm_output = self.model.query(messages)
                print("LM output:", lm_output)

                # 2. 记录 LM 的回复
                messages.append({"role": "assistant", "content": lm_output})

                # 3. 解析动作
                action = parse_action(lm_output)
                print("Action:", action)

                # 4. 退出条件
                if action == "exit":
                    break

                # 5. 格式检查
                if action == "":
                    messages.append({"role": "user", "content": (
                        "Please either provide a command in "
                        "'''bash-action\\n<command>\\n''' format "
                        "or put 'exit' inside the bash-action block to finish."
                    )})
                    continue

                # 6. 执行动作
                output = self.environment.execute(action)
                print("Output:", output)

                # 7. 把执行结果发回 LM
                messages.append({"role": "user", "content": output})

            except KeyboardInterrupt:
                break
            except Exception as e:
                # 把异常变成 user message，让循环继续
                messages.append({"role": "user", "content": f"Error: {e}"})

        return messages


# 向后兼容：延迟创建，避免 import 时就需要 API key
_default_agent: Agent | None = None


def run(task: str) -> list[dict[str, str]]:
    global _default_agent
    if _default_agent is None:
        from src.mini_agent.model import Model            # noqa: E402
        from src.mini_agent.environment import Environment  # noqa: E402
        _default_agent = Agent(Model(), Environment())
    return _default_agent.run(task)

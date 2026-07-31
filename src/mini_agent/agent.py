"""Agent 主循环 — 把模型、解析、执行串起来."""

from src.mini_agent.model import query_lm
from src.mini_agent.parser import parse_action
from src.mini_agent.environment import execute_action


SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "When you want to run a command, wrap it in ```bash-action\n<command>\n```. "
    "To finish, run the exit command."
)


def run(task: str) -> list[dict[str, str]]:
    """Run the agent loop for a given user task.

    Returns the full message history.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    while True:
        try:
            while True:
                # 1. 查询 LM
                lm_output = query_lm(messages)
                print("LM output:", lm_output)

                # 2. 记录 LM 的回复
                messages.append({"role": "assistant", "content": lm_output})

                # 3. 解析动作
                action = parse_action(lm_output)
                print("Action:", action)

                # 4. 退出条件
                if action == "exit":
                    break

                # 5.
                if action == "":
                    messages.append({"role":"user","content":"Please either provide a command in '''bash-action\n<command>\n''' format or put 'exit' inside the bash-action block to finish."})
                    continue

                # 5. 执行动作
                output = execute_action(action)
                print("Output:", output)

                # 6. 把执行结果发回 LM
                messages.append({"role": "user", "content": output})
        except KeyboardInterrupt:
            break
        except Exception as e:
            # 把异常变成 user message, 让循环继续
            messages.append({"role":"user","content":f"Error: {e}"})
    return messages

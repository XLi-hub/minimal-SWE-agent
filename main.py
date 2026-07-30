"""入口：启动 agent 处理用户任务."""

from src.mini_agent.agent import run


if __name__ == "__main__":
    task = input("Task: ")
    run(task)

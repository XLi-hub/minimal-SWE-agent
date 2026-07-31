"""入口：启动 agent 处理用户任务."""

from src.mini_agent.agent import Agent
from src.mini_agent.model import Model
from src.mini_agent.environment import Environment


if __name__ == "__main__":
    task = input("Task: ")
    agent = Agent(Model(), Environment())
    agent.run(task)

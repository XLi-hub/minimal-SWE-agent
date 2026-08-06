"""入口：启动 agent 处理用户任务.

Usage::

    # 日常 — 本地环境（默认）
    python main.py

    # Docker 环境
    python main.py --env docker --image python:3.11-slim

    # 自定义工作目录
    python main.py --env docker --image python:3.11-slim --cwd /workspace
"""

import argparse

from src.mini_agent.agent import Agent
from src.mini_agent.model import Model
from src.mini_agent.environments import get_environment


def _parse_args():
    p = argparse.ArgumentParser(description="mini-agent")
    p.add_argument(
        "--env", default="local",
        choices=["local", "docker"],
        help="Environment type (default: local)",
    )
    p.add_argument(
        "--image", default="python:3.11-slim",
        help="Docker image (only for --env docker)",
    )
    p.add_argument(
        "--cwd", default="/",
        help="Working directory inside the container (only for --env docker)",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # 工厂创建环境
    kwargs = {}
    if args.env == "docker":
        kwargs["image"] = args.image
        kwargs["cwd"] = args.cwd
    env = get_environment(args.env, **kwargs)

    task = input("Task: ")
    agent = Agent(Model(), env)
    result = agent.run(task)

    print(f"\nExit status: {result['exit_status']}")
    if result["submission"]:
        print(f"Submission:\n{result['submission']}")

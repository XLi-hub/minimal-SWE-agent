"""入口：启动 agent 处理用户任务.

Usage::

    # 日常 — 本地环境（默认）
    python main.py

    # Docker 环境
    python main.py --env docker --image python:3.11-slim

    # 自定义工作目录
    python main.py --env docker --image python:3.11-slim --cwd /workspace

    # 用 YAML 文件覆盖配置
    python main.py --config my_config.yaml

    # 用点号 key=value 覆盖单项配置（可重复）
    python main.py -c agent.max_steps=50 -c agent.cost_limit=5

    # 用环境变量覆盖配置（__ 为嵌套分隔符）
    MINI_AGENT_AGENT__MAX_STEPS=500 python main.py

    # 保存轨迹到 .traj.json
    python main.py --task "修一下 bug" -o last_run.traj.json

    # 强制上下文压缩（用极小窗口触发）
    python main.py --context-window 2000 --task "修一下 bug"

配置优先级（低 → 高）：内置 default.yaml < --config（从左到右）
< MINI_AGENT_* 环境变量 < CLI 参数。详见 docs/config.md。
"""

import argparse

from src.mini_agent.agent import Agent
from src.mini_agent.config import UNSET, build_config
from src.mini_agent.model import Model
from src.mini_agent.environments import get_environment


def _parse_args():
    p = argparse.ArgumentParser(description="mini-agent")
    p.add_argument(
        "-c", "--config", action="append", default=[],
        metavar="SPEC",
        help="YAML 文件路径，或 key=value 点号路径（如 agent.max_steps=50）。"
             "可重复，后写优先。",
    )
    p.add_argument(
        "--env", default=None,
        choices=["local", "docker"],
        help="Environment type (default from config: local)",
    )
    p.add_argument(
        "--image", default=None,
        help="Docker image (only for --env docker; default from config)",
    )
    p.add_argument(
        "--cwd", default=None,
        help="Working directory inside the container (only for --env docker)",
    )
    p.add_argument(
        "--max-steps", type=int, default=None,
        help="Maximum tool-calling iterations (default from config: 250)",
    )
    p.add_argument(
        "--max-time", type=float, default=None,
        help="Maximum wall-clock time in seconds (default from config: 1800)",
    )
    p.add_argument(
        "-t", "--task", default=None,
        help="Task to run (if omitted, prompt interactively)",
    )
    p.add_argument(
        "-o", "--output", default=None,
        help="Save the trajectory to this file (e.g. last_run.traj.json)",
    )
    p.add_argument(
        "--cost-limit", type=float, default=None,
        help="Stop when accumulated cost exceeds this USD value "
             "(default from config: 3.0, 0 disables)",
    )
    p.add_argument(
        "--context-window", type=int, default=None,
        help="Estimated context window in tokens (default from config: 64000). "
             "Set small to force context compression.",
    )
    p.add_argument(
        "--keep-last-n-turns", type=int, default=None,
        help="Most-recent round-trip turns kept verbatim after compression "
             "(default from config: 4)",
    )
    return p.parse_args()


def _u(value):
    """None → UNSET，让「未指定的 CLI 参数」不覆盖下层配置。"""
    return UNSET if value is None else value


if __name__ == "__main__":
    args = _parse_args()

    # 合并所有配置来源：default.yaml < --config < 环境变量 < CLI
    config = build_config(
        config_specs=args.config,
        cli_overrides={
            "agent": {
                "max_steps": _u(args.max_steps),
                "max_time": _u(args.max_time),
                "cost_limit": _u(args.cost_limit),
                "context_window": _u(args.context_window),
                "keep_last_n_turns": _u(args.keep_last_n_turns),
            },
            "environment": {
                "type": _u(args.env),
                "image": _u(args.image),
                "cwd": _u(args.cwd),
            },
        },
    )

    # 工厂创建环境（type 决定 local / docker）
    env = get_environment(config.environment.type, config=config.environment)

    task = args.task if args.task else input("Task: ")
    agent = Agent(Model(config.model), env, config=config)
    result = agent.run(task, output=args.output)

    print(f"\nExit status: {result['exit_status']}")
    if result["submission"]:
        print(f"Submission:\n{result['submission']}")

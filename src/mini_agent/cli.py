"""CLI 入口：解析参数 → 合并配置 → 组装 Agent → 运行.

安装后有两种等价调用方式：::

    minimal                       # console script（pip install 后）
    python -m mini_agent       # 不装也能用（只要包可 import）

用法示例：::

    # 日常 — 本地环境（默认，交互式输入 task）
    minimal

    # 直接给任务
    minimal --task "修一下 bug"

    # Docker 环境
    minimal --env docker --image python:3.11-slim

    # 自定义工作目录
    minimal --env docker --image python:3.11-slim --cwd /workspace

    # 用 YAML 文件覆盖配置
    minimal --config my_config.yaml

    # 内置两套工具配置：default.yaml（默认 5 工具）/ default_bash.yaml（旧路线 2 工具）
    minimal --config default_bash

    # 用点号 key=value 覆盖单项配置（可重复）
    minimal -c agent.max_steps=50 -c agent.cost_limit=5

    # 用环境变量覆盖配置（__ 为嵌套分隔符）
    MINI_AGENT_AGENT__MAX_STEPS=500 minimal

    # 保存轨迹到 .traj.json
    minimal --task "修一下 bug" -o last_run.traj.json

    # 强制上下文压缩（用极小窗口触发）
    minimal --context-window 2000 --task "修一下 bug"

配置优先级（低 → 高）：内置 default.yaml < --config（从左到右）
< MINI_AGENT_* 环境变量 < CLI 参数。详见 docs/guides/configuration.md。
"""

import argparse
import sys
from typing import Sequence

from mini_agent import __version__
from mini_agent.agent import Agent
from mini_agent.config import UNSET, build_config
from mini_agent.environments import get_environment
from mini_agent.model import Model


# Keep the exit contract of the command-line interface explicit.  ``Agent``
# returns a descriptive string because it is also used as a library; callers
# of a CLI need a process status instead.
EXIT_CODES = {
    "submitted": 0,
    "error": 1,
    "no_tool_calls": 2,
    "max_steps": 3,
    "max_time": 4,
    "cost_limit": 5,
    # 128 + SIGINT is the conventional Unix status for Ctrl+C.
    "interrupted": 130,
}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="mini-agent")
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
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
    return p.parse_args(argv)


def _u(value):
    """None → UNSET，让「未指定的 CLI 参数」不覆盖下层配置。"""
    return UNSET if value is None else value


def _release(resource, method: str, label: str) -> None:
    """Release one CLI-owned resource without hiding the run failure.

    Resource construction is intentionally allowed to fail before a resource
    is bound, so callers initialize their variables to ``None``.  A cleanup
    failure is reported as a warning and ignored: especially in a ``finally``
    block, raising it would replace the useful exception from the actual run.
    ``getattr`` keeps the CLI tests and third-party adapters compatible with
    lightweight objects that do not own resources.
    """
    if resource is None:
        return
    closer = getattr(resource, method, None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception as exc:  # pragma: no cover - warning path is tested via main
        print(f"Warning: failed to release {label}: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

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

    # Initialize before entering the try block so a constructor that fails
    # halfway through cannot leave ``finally`` referencing an unbound name.
    env = None
    model = None
    try:
        # 工厂创建环境（type 决定 local / docker）
        env = get_environment(config.environment.type, config=config.environment)

        # ``None`` means the flag was omitted; an explicitly empty task is a
        # valid non-interactive task and must not unexpectedly prompt stdin.
        task = args.task if args.task is not None else input("Task: ")
        model = Model(config.model)
        agent = Agent(model, env, config=config)
        result = agent.run(task, output=args.output)

        status = result.get("exit_status") or "error"
        print(f"\nExit status: {status}")
        if result.get("submission"):
            print(f"Submission:\n{result['submission']}")
        return EXIT_CODES.get(status, EXIT_CODES["error"])
    finally:
        # Close the model before tearing down its execution environment.  Both
        # calls are best-effort and run on success, Agent failures, and
        # constructor/input exceptions alike.
        _release(model, "close", "model")
        _release(env, "cleanup", "environment")


if __name__ == "__main__":
    raise SystemExit(main())

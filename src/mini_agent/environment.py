"""执行环境模块 — 在终端中运行命令."""

import os
import subprocess


def execute_action(command: str, timeout: int = 30) -> str:
    """Run a shell command and return its combined stdout+stderr."""
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        env=os.environ,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return result.stdout

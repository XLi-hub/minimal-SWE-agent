"""执行环境模块 — 在终端中运行命令."""

import os
import subprocess

# 关掉交互式的分页器和进度条，防止 agent 卡死
ENV = {
    "PAGER": "cat",              # man / git diff 不再等按键
    "MANPAGER": "cat",           # man 命令专用
    "LESS": "-R",                # 即使调了 less 也不交互
    "PIP_PROGRESS_BAR": "off",   # pip install 不打印进度条
    "TQDM_DISABLE": "1",         # 关掉 tqdm 进度条
}


class Environment:
    """执行 shell 命令的环境。"""

    def execute(self, command: str, timeout: int = 30) -> str:
        """Run a shell command and return its combined stdout+stderr."""
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            env=os.environ | ENV,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return result.stdout


# 向后兼容：保留函数形式的快捷方式
execute_action = Environment().execute

"""Local shell execution — runs commands directly on the host."""

import os
import subprocess

from src.mini_agent.environments import Environment

# Disable interactive pagers and progress bars so the agent doesn't hang.
_ENV = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}


class LocalEnvironment(Environment):
    """Execute shell commands directly on the local machine."""

    def execute(self, command: str, timeout: int = 30) -> str:
        """Run a shell command and return its combined stdout+stderr.

        If the command does not finish within *timeout* seconds the
        partial output collected so far is returned together with a
        timeout marker.  The underlying process is **not** killed —
        long-running commands like ``pip install`` are allowed to
        continue.
        """
        proc = subprocess.Popen(
            command,
            shell=True,
            text=True,
            env=os.environ | _ENV,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            return stdout
        except subprocess.TimeoutExpired:
            # Process is still alive — don't kill it, just raise so the
            # Agent can format the partial output for the model.
            raise

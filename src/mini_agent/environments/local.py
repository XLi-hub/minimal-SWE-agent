"""Local shell execution — runs commands directly on the host."""

import os
import subprocess
from pathlib import Path

from mini_agent.config import EnvironmentConfig, get_default_config
from mini_agent.environments import Environment


class LocalEnvironment(Environment):
    """Execute shell commands directly on the local machine.

    ``config`` supplies the environment variables (pager/progress-bar
    overrides) and the default per-command timeout.
    """

    def __init__(self, config: EnvironmentConfig | None = None) -> None:
        self.config = config or get_default_config().environment

    def execute(self, command: str, timeout: int | None = None) -> str:
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
            env=os.environ | self.config.env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = proc.communicate(
                timeout=timeout if timeout is not None else self.config.timeout,
            )
            return stdout
        except subprocess.TimeoutExpired:
            # Process is still alive — don't kill it, just raise so the
            # Agent can format the partial output for the model.
            raise

    def read_file(self, path: str) -> str:
        """Read *path* relative to the host process cwd — the SAME cwd ``bash`` uses.

        ``execute`` runs ``Popen(..., shell=True)`` with no ``cwd=``, so it inherits
        the process cwd.  Resolving against the process cwd here keeps the file tools
        and ``bash`` seeing the same tree.
        """
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

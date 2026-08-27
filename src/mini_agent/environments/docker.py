"""Docker container execution — runs commands inside an isolated container."""

import os
import subprocess
import uuid

from mini_agent.config import EnvironmentConfig, get_default_config
from mini_agent.environments import Environment, ExecutionResult
from mini_agent.environments.process import (
    communicate_with_timeout,
    run_process,
)


class DockerEnvironment(Environment):
    """Execute shell commands inside a Docker container.

    The container is started on construction and kept alive with ``sleep``.
    Each :meth:`execute` call runs via ``docker exec``.

    Parameters
    ----------
    image:
        Docker image to use (e.g. ``"python:3.11-slim"``).
    cwd:
        Working directory inside the container.
    env:
        Extra environment variables to set in the container.
    forward_env:
        Host environment variable names to forward when executing commands.
    executable:
        Container executable (normally ``docker``; useful for podman or tests).
    run_args:
        Extra arguments placed on the ``docker run`` command line.
    pull_timeout:
        Maximum seconds allowed for startup/image pulling.
    interpreter:
        Command and arguments used to interpret the command string inside the
        container (default ``["bash", "-lc"]``).
    timeout:
        Per-command timeout in seconds (default 30).
    container_timeout:
        Max container lifetime, passed to ``sleep`` (default ``"2h"``).
    """

    def __init__(
        self,
        image: str | None = None,
        *,
        config: EnvironmentConfig | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        container_timeout: str | None = None,
        forward_env: list[str] | None = None,
        executable: str | None = None,
        run_args: list[str] | None = None,
        pull_timeout: int | None = None,
        interpreter: list[str] | None = None,
    ):
        # 显式参数优先，否则回落到 default.yaml 里的 environment 配置。
        cfg = config or get_default_config().environment
        self._image = image if image is not None else cfg.image
        self._cwd = cwd if cwd is not None else cfg.cwd
        self._env = dict(env) if env is not None else dict(cfg.env)
        self._timeout = timeout if timeout is not None else cfg.timeout
        self._container_timeout = (
            container_timeout if container_timeout is not None else cfg.container_timeout
        )
        self._forward_env = (
            list(forward_env) if forward_env is not None else list(cfg.forward_env)
        )
        self._executable = executable if executable is not None else cfg.executable
        self._run_args = list(run_args) if run_args is not None else list(cfg.run_args)
        self._pull_timeout = pull_timeout if pull_timeout is not None else cfg.pull_timeout
        self._interpreter = (
            list(interpreter) if interpreter is not None else list(cfg.interpreter)
        )
        self._container_id: str | None = None
        self._start_container()

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def execute(self, command: str, timeout: int | None = None) -> ExecutionResult:
        """Run *command* inside the container and return a result mapping."""
        if self._container_id is None:
            raise RuntimeError("Container has not been started")

        cmd = [self._executable, "exec", "-w", self._cwd]
        # Forward selected host variables first; explicit container values
        # below intentionally win on conflicts.
        for key in self._forward_env:
            if (value := os.getenv(key)) is not None:
                cmd.extend(["-e", f"{key}={value}"])
        for key, value in self._env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self._container_id, *self._interpreter, command])

        return run_process(
            cmd,
            timeout=timeout if timeout is not None else self._timeout,
            # Keep diagnostics focused on the model-supplied command.  The
            # full docker argv can contain forwarded environment secrets.
            display_command=command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )

    def read_file(self, path: str) -> str:
        if self._container_id is None:
            raise RuntimeError("Container has not been started")
        cmd = [self._executable, "exec", "-w", self._cwd, self._container_id,
               "cat", "--", path]
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        out, _ = communicate_with_timeout(proc, timeout=self._timeout)
        if proc.returncode != 0:
            if "no such file" in out.lower():
                raise FileNotFoundError(out.strip() or path)
            raise OSError(out.strip() or f"cannot read {path!r}")
        return out

    def write_file(self, path: str, content: str) -> None:
        if self._container_id is None:
            raise RuntimeError("Container has not been started")
        # content on stdin (never quoted); path as positional "$1" (never
        # interpolated).  `cat > "$1"` preserves an existing file's mode.
        cmd = [self._executable, "exec", "-i", "-w", self._cwd, self._container_id,
               "sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", path]
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        out, _ = communicate_with_timeout(
            proc,
            input=content,
            timeout=self._timeout,
        )
        if proc.returncode != 0:
            raise OSError(out.strip() or f"failed to write {path!r}")

    def cleanup(self) -> None:
        """Stop and remove the Docker container."""
        if self._container_id is None:
            return
        container_id = self._container_id
        self._container_id = None
        try:
            stopped = subprocess.run(
                [self._executable, "stop", container_id],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if stopped.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
        # ``docker stop`` may fail when the container has already exited. The
        # force-remove fallback is deliberately synchronous so benchmark
        # workers do not race with the next instance's container.
        try:
            subprocess.run(
                [self._executable, "rm", "-f", container_id],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _start_container(self) -> None:
        """Launch the container in detached mode with a long sleep."""
        container_name = f"mini-agent-{uuid.uuid4().hex[:8]}"
        cmd = [
            self._executable, "run", "-d",
            "--name", container_name,
            "-w", self._cwd,
            *self._run_args,
            self._image,
            "sleep", self._container_timeout,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._pull_timeout,
            check=True,
        )
        self._container_id = result.stdout.strip()

    def __del__(self) -> None:
        """Best-effort cleanup on garbage collection."""
        try:
            self.cleanup()
        except Exception:
            # Destructors must never surface errors during interpreter shutdown.
            pass

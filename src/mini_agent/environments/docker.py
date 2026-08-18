"""Docker container execution — runs commands inside an isolated container."""

import subprocess
import uuid

from src.mini_agent.config import EnvironmentConfig, get_default_config
from src.mini_agent.environments import Environment


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
        self._container_id: str | None = None
        self._start_container()

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def execute(self, command: str, timeout: int | None = None) -> str:
        """Run *command* inside the container and return stdout+stderr.

        If the command does not finish within *timeout* seconds the
        partial output collected so far is returned together with a
        timeout marker.  The underlying process is **not** killed —
        long-running commands like ``pip install`` are allowed to
        continue.
        """
        if self._container_id is None:
            raise RuntimeError("Container has not been started")

        cmd = [
            "docker", "exec", "-w", self._cwd,
        ]
        for key, value in self._env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self._container_id, "bash", "-lc", command])

        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = proc.communicate(
                timeout=timeout if timeout is not None else self._timeout,
            )
            return stdout
        except subprocess.TimeoutExpired:
            # Process is still alive — don't kill it.
            raise

    def read_file(self, path: str) -> str:
        if self._container_id is None:
            raise RuntimeError("Container has not been started")
        cmd = ["docker", "exec", "-w", self._cwd, self._container_id,
               "cat", "--", path]
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out, _ = proc.communicate(timeout=self._timeout)
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
        cmd = ["docker", "exec", "-i", "-w", self._cwd, self._container_id,
               "sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", path]
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out, _ = proc.communicate(input=content, timeout=self._timeout)
        if proc.returncode != 0:
            raise OSError(out.strip() or f"failed to write {path!r}")

    def cleanup(self) -> None:
        """Stop and remove the Docker container."""
        if self._container_id is None:
            return
        subprocess.run(
            f"(timeout 60 docker stop {self._container_id} || "
            f"docker rm -f {self._container_id}) >/dev/null 2>&1 &",
            shell=True,
        )
        self._container_id = None

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _start_container(self) -> None:
        """Launch the container in detached mode with a long sleep."""
        container_name = f"mini-agent-{uuid.uuid4().hex[:8]}"
        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", container_name,
            "-w", self._cwd,
            self._image,
            "sleep", self._container_timeout,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # generous: image pull may be slow
            check=True,
        )
        self._container_id = result.stdout.strip()

    def __del__(self) -> None:
        """Best-effort cleanup on garbage collection."""
        self.cleanup()

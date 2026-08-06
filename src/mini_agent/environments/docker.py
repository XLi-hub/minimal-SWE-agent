"""Docker container execution — runs commands inside an isolated container."""

import subprocess
import uuid

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
        image: str,
        *,
        cwd: str = "/",
        env: dict[str, str] | None = None,
        timeout: int = 30,
        container_timeout: str = "2h",
    ):
        self._image = image
        self._cwd = cwd
        self._env = dict(env) if env else {}
        self._timeout = timeout
        self._container_timeout = container_timeout
        self._container_id: str | None = None
        self._start_container()

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def execute(self, command: str, timeout: int | None = None) -> str:
        """Run *command* inside the container and return stdout+stderr."""
        if self._container_id is None:
            raise RuntimeError("Container has not been started")

        cmd = [
            "docker", "exec", "-w", self._cwd,
        ]
        for key, value in self._env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self._container_id, "bash", "-lc", command])

        try:
            result = subprocess.run(
                cmd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout if timeout is not None else self._timeout,
            )
            return result.stdout
        except Exception:
            # Let the Agent's exception handler deal with it.
            raise

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

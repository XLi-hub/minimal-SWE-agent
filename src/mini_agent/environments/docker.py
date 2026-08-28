"""Docker container execution — runs commands inside an isolated container."""

import os
import signal
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

from mini_agent.config import EnvironmentConfig, get_default_config
from mini_agent.environments import Environment, ExecutionResult


_MISSING = object()


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
        cmd, pid_path = self._tracked_exec_command(
            cmd,
            [*self._interpreter, command],
        )

        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                cmd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
            stdout, _ = _communicate_with_timeout(
                process,
                timeout=timeout if timeout is not None else self._timeout,
                on_timeout=lambda: self._kill_tracked_process(pid_path),
            )
            return ExecutionResult(
                output=_decode_output(stdout),
                returncode=_returncode(process),
            )
        except subprocess.TimeoutExpired as exc:
            partial = _decode_output(getattr(exc, "stdout", None))
            return ExecutionResult(
                output=partial,
                returncode=-1,
                exception_info=f"Command timed out after {exc.timeout} seconds: {command}",
            )
        except Exception as exc:
            output = _decode_output(getattr(exc, "output", None))
            return ExecutionResult(
                output=output,
                returncode=-1,
                exception_info=f"An error occurred while executing the command: {exc}",
            )

    def read_file(self, path: str) -> str:
        if self._container_id is None:
            raise RuntimeError("Container has not been started")
        cmd, pid_path = self._tracked_exec_command(
            [self._executable, "exec", "-w", self._cwd],
            ["cat", "--", path],
        )
        proc = subprocess.Popen(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        out, _ = _communicate_with_timeout(
            proc,
            timeout=self._timeout,
            on_timeout=lambda: self._kill_tracked_process(pid_path),
        )
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
        cmd, pid_path = self._tracked_exec_command(
            [self._executable, "exec", "-i", "-w", self._cwd],
            ["sh", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", path],
        )
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
        out, _ = _communicate_with_timeout(
            proc,
            input=content,
            timeout=self._timeout,
            on_timeout=lambda: self._kill_tracked_process(pid_path),
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

    def _tracked_exec_command(
        self,
        prefix: list[str],
        payload: list[str],
    ) -> tuple[list[str], str]:
        """Wrap a Docker exec payload in a killable container process group.

        ``docker exec`` does not promise that the process it starts has its
        own process group.  Blindly sending ``SIGKILL`` to ``-PID`` can
        therefore either leave descendants behind or target the container's
        long-running PID 1 when the exec inherited its process group.  The
        launcher creates a new session when ``setsid`` is available and
        records which cleanup strategy is safe to use.  Minimal images that
        do not ship ``setsid`` use an exact ``/proc`` child-tree fallback.
        """
        if self._container_id is None:
            raise RuntimeError("Container has not been started")
        pid_path = f"/tmp/mini-agent-exec-{uuid.uuid4().hex}.pid"
        launcher = (
            "if command -v setsid >/dev/null 2>&1; then "
            # Non-interactive POSIX shells connect an asynchronous command's
            # stdin to /dev/null unless it has an explicit redirection.  Keep
            # a duplicate alive so tracked ``docker exec -i`` writes do not
            # truncate their target and immediately receive EOF.
            "exec 3<&0; MINI_AGENT_EXEC_MODE=group setsid \"$@\" <&3 & "
            "child=$!; exec 3<&-; wait \"$child\"; status=$?; exit \"$status\"; "
            "else MINI_AGENT_EXEC_MODE=tree exec \"$@\"; fi"
        )
        tracker = (
            'pid_file=$1; shift; '
            'trap \'rm -f "$pid_file"\' EXIT; '
            'printf "%s %s\\n" "$$" "${MINI_AGENT_EXEC_MODE:-tree}" > "$pid_file"; '
            '"$@"'
        )
        return [
            *prefix,
            self._container_id,
            "sh",
            "-c",
            launcher,
            "mini-agent-launcher",
            "sh",
            "-c",
            tracker,
            "mini-agent-exec",
            pid_path,
            *payload,
        ], pid_path

    def _kill_tracked_process(self, pid_path: str) -> None:
        """Kill a timed-out Docker exec process tree without touching PID 1."""
        if self._container_id is None:
            return
        cleanup = (
            'pid_file=$1; pid=""; mode=""; '
            'if read -r pid mode < "$pid_file" 2>/dev/null; then '
            'case "$pid" in ""|*[!0-9]*) pid="";; esac; '
            'if [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null; then '
            'if [ "$mode" = group ]; then '
            # POSIX ``kill`` accepts a negative process-group id, but dash's
            # builtin parses it as an invalid option.  Docker always exposes
            # /proc, so select group members explicitly and stop them before
            # killing them to prevent new descendants during cleanup.
            'for sig in STOP KILL; do '
            'for proc in /proc/[0-9]*; do '
            'stat=$(cat "$proc/stat" 2>/dev/null) || continue; '
            'rest=${stat#*) }; set -- $rest; '
            '[ "${3:-}" = "$pid" ] || continue; '
            'member=${proc##*/}; [ "$member" -gt 1 ] 2>/dev/null || continue; '
            'kill -"$sig" "$member" 2>/dev/null || true; '
            'done; '
            'done; '
            'else '
            'kill_tree() { '
            'kill -STOP "$1" 2>/dev/null || true; '
            'children="/proc/$1/task/$1/children"; '
            'if [ -r "$children" ]; then '
            'for child in $(cat "$children" 2>/dev/null); do kill_tree "$child"; done; '
            'fi; '
            'kill -KILL "$1" 2>/dev/null || true; '
            '}; '
            'kill_tree "$pid"; '
            'fi; '
            'fi; '
            'fi; '
            'rm -f "$pid_file"'
        )
        subprocess.run(
            [
                self._executable,
                "exec",
                self._container_id,
                "sh",
                "-c",
                cleanup,
                "mini-agent-cleanup",
                pid_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

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


def _returncode(process: Any) -> int:
    value = getattr(process, "returncode", -1)
    return value if isinstance(value, int) else 0


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
    except ProcessLookupError:
        pass
    except (OSError, TypeError, ValueError):
        try:
            process.kill()
        except (ProcessLookupError, OSError, TypeError, ValueError):
            pass


def _communicate_with_timeout(
    process: subprocess.Popen,
    *,
    timeout: float | None,
    input: Any = _MISSING,
    on_timeout: Callable[[], None] | None = None,
) -> tuple[Any, Any]:
    """Communicate with a process, killing both host and backend work on timeout."""
    try:
        if input is _MISSING:
            return process.communicate(timeout=timeout)
        return process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Clean up the container-side process while the tracking wrapper (and
        # its PID file) is still alive.  Killing the host ``docker exec``
        # client first can make the wrapper exit and remove that evidence,
        # leaving detached descendants running in the container.
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                pass
        _terminate_process_group(process)
        try:
            tail, _ = process.communicate()
        except Exception:
            tail = ""
        if tail:
            exc.stdout = tail
        raise

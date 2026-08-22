"""Local shell execution — runs commands directly on the host."""

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from mini_agent.config import EnvironmentConfig, get_default_config
from mini_agent.environments import Environment, ExecutionResult


class LocalEnvironment(Environment):
    """Execute shell commands directly on the local machine.

    ``config`` supplies the environment variables (pager/progress-bar
    overrides) and the default per-command timeout.
    """

    def __init__(self, config: EnvironmentConfig | None = None) -> None:
        self.config = config or get_default_config().environment

    def execute(self, command: str, timeout: int | None = None) -> ExecutionResult:
        """Run a shell command and return a normalized execution result.

        Commands are put in their own process group on POSIX. If a command
        times out, the complete group is killed before collecting the remaining
        output, so child processes cannot outlive the tool call.
        """
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                text=True,
                env=os.environ | self.config.env,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # A separate session makes the shell PID the process-group
                # leader, so killpg() also terminates descendants.
                start_new_session=os.name == "posix",
            )
            stdout, _ = process.communicate(
                timeout=timeout if timeout is not None else self.config.timeout,
            )
            return ExecutionResult(
                output=_decode_output(stdout),
                returncode=_returncode(process),
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired.stdout may be bytes even with text=True.
            partial = _decode_output(getattr(exc, "stdout", None))
            if process is not None:
                _terminate_process_group(process)
                try:
                    tail, _ = process.communicate()
                except Exception:
                    tail = ""
                # communicate() returns the complete buffered stream after
                # the kill on CPython, not just bytes read after the timeout.
                # Prefer it when available to avoid duplicating partial output.
                if tail:
                    partial = _decode_output(tail)
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


def _returncode(process: Any) -> int:
    """Return an integer status even when a process object is mocked."""
    value = getattr(process, "returncode", -1)
    return value if isinstance(value, int) else 0


def _decode_output(value: Any) -> str:
    """Normalize subprocess output from either text or bytes."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _terminate_process_group(process: subprocess.Popen) -> None:
    """Forcefully terminate *process* and all descendants where possible."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        # The command exited between TimeoutExpired and cleanup.
        pass
    except (OSError, TypeError, ValueError):
        # Fall back to a direct process kill if a process group cannot be
        # addressed (for example on a platform with restricted job control).
        try:
            process.kill()
        except (ProcessLookupError, OSError, TypeError, ValueError):
            pass

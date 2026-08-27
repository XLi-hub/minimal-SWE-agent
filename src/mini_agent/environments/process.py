"""Shared subprocess lifecycle helpers for environment implementations."""

import os
import signal
import subprocess
from collections.abc import Callable
from typing import Any

from mini_agent.environments import ExecutionResult


_MISSING = object()


def run_process(
    command: Any,
    *,
    timeout: float | None,
    display_command: Any = _MISSING,
    on_timeout: Callable[[], None] | None = None,
    **popen_kwargs: Any,
) -> ExecutionResult:
    """Run a subprocess and normalize its result for an environment.

    A timed-out process is killed together with its process group and then
    reaped before a result is returned.  Keeping this lifecycle in one place
    makes local and container-backed command execution behave identically.
    """
    process: subprocess.Popen | None = None
    shown_command = command if display_command is _MISSING else display_command
    try:
        process = subprocess.Popen(command, **popen_kwargs)
        stdout, _ = communicate_with_timeout(
            process,
            timeout=timeout,
            on_timeout=on_timeout,
        )
        return ExecutionResult(
            output=decode_output(stdout),
            returncode=returncode(process),
        )
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired.stdout may be bytes even with text=True.
        partial = decode_output(getattr(exc, "stdout", None))
        return ExecutionResult(
            output=partial,
            returncode=-1,
            exception_info=f"Command timed out after {exc.timeout} seconds: {shown_command}",
        )
    except Exception as exc:
        output = decode_output(getattr(exc, "output", None))
        return ExecutionResult(
            output=output,
            returncode=-1,
            exception_info=f"An error occurred while executing the command: {exc}",
        )


def communicate_with_timeout(
    process: subprocess.Popen,
    *,
    timeout: float | None,
    input: Any = _MISSING,
    on_timeout: Callable[[], None] | None = None,
) -> tuple[Any, Any]:
    """Communicate with *process*, killing and reaping it on timeout.

    ``input`` is omitted when not supplied so callers preserve the exact
    ``Popen.communicate(timeout=...)`` invocation used by read operations and
    existing mocks.  A timeout is re-raised after cleanup for callers such as
    Docker file operations that need to retain their existing exception
    contract.
    """
    try:
        if input is _MISSING:
            return process.communicate(timeout=timeout)
        return process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_group(process)
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                # Timeout cleanup is best effort.  The original timeout must
                # remain the observable failure even if backend-specific
                # cleanup (for example a second Docker exec) is unavailable.
                pass
        # Keep the partial output available to callers handling the original
        # exception.  This also lets run_process preserve its prior output
        # preference after the process has been killed.
        try:
            tail, _ = process.communicate()
        except Exception:
            tail = ""
        if tail:
            exc.stdout = tail
        raise


def decode_output(value: Any) -> str:
    """Normalize subprocess output from either text or bytes."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def returncode(process: Any) -> int:
    """Return an integer status even when a process object is mocked."""
    value = getattr(process, "returncode", -1)
    return value if isinstance(value, int) else 0


def terminate_process_group(process: subprocess.Popen) -> None:
    """Forcefully terminate *process* and all descendants where possible."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            # ``Popen.kill`` only terminates the direct child on Windows.
            # taskkill /T walks the descendant tree, matching POSIX killpg as
            # closely as the standard Windows tools allow.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
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

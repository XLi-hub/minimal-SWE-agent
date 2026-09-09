"""Core interfaces and result types for execution environments."""

from abc import ABC, abstractmethod
from typing import Any


class ExecutionResult(dict[str, Any]):
    """The normalized result of executing a command.

    Environments deliberately return a mapping rather than raising for a
    command's exit status.  A non-zero status is a perfectly normal shell
    result (and useful information for the model); ``exception_info`` is
    reserved for failures in the execution machinery itself, such as a
    timeout or an unavailable Docker daemon.
    """

    def __init__(
        self,
        output: str = "",
        returncode: int = 0,
        exception_info: str = "",
    ) -> None:
        super().__init__(
            output=output,
            returncode=returncode,
            exception_info=exception_info,
        )

    def __contains__(self, item: object) -> bool:
        """Keep the old ``"text" in env.execute(...)`` convenience working."""
        if isinstance(item, str) and not super().__contains__(item):
            return item in self["output"]
        return super().__contains__(item)

    def strip(self, *args: Any, **kwargs: Any) -> str:
        """Delegate ``strip`` to output for legacy callers."""
        return self["output"].strip(*args, **kwargs)


class Environment(ABC):
    """Abstract execution environment."""

    @abstractmethod
    def execute(self, command: str, timeout: int | None = 30) -> ExecutionResult:
        """Run *command* and return output, status, and execution errors.

        Implementations should capture stdout and stderr together.  A command
        that exits non-zero should return that status in ``returncode`` rather
        than raising; exceptions raised while starting/running the command
        should be represented by ``returncode=-1`` and ``exception_info``.
        """
        ...

    @abstractmethod
    def read_file(self, path: str, timeout: float | None = None) -> str:
        """Return UTF-8 contents, honoring *timeout* when the backend can block."""
        ...

    @abstractmethod
    def write_file(
        self, path: str, content: str, timeout: float | None = None
    ) -> None:
        """Write content, creating parents and honoring backend *timeout* support."""
        ...

    def cleanup(self) -> None:
        """Release any resources held by the environment.

        The default implementation is a no-op.  Subclasses that acquire
        resources (e.g. Docker containers) should override this.
        """


__all__ = ["Environment", "ExecutionResult"]

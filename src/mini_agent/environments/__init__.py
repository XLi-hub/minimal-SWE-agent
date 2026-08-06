"""Environment implementations for the agent.

Provides a common :class:`Environment` interface and two built-in
implementations:

* :class:`LocalEnvironment` — execute commands directly on the host.
* :class:`DockerEnvironment` — execute commands inside a Docker container.
"""

from abc import ABC, abstractmethod


class Environment(ABC):
    """Abstract execution environment."""

    @abstractmethod
    def execute(self, command: str, timeout: int = 30) -> str:
        """Run *command* in a shell and return combined stdout + stderr."""
        ...

    def cleanup(self) -> None:
        """Release any resources held by the environment.

        The default implementation is a no-op.  Subclasses that acquire
        resources (e.g. Docker containers) should override this.
        """


from src.mini_agent.environments.local import LocalEnvironment  # noqa: E402, F401
from src.mini_agent.environments.docker import DockerEnvironment  # noqa: E402, F401

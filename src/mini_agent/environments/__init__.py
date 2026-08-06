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

# ---------------------------------------------------------------------------
# factory — resolve a name string to an Environment instance
# ---------------------------------------------------------------------------

_MAPPING: dict[str, type[Environment]] = {
    "local": LocalEnvironment,
    "docker": DockerEnvironment,
}


def get_environment(name: str, **kwargs) -> Environment:
    """Create an environment by name.

    ``"local"`` → :class:`LocalEnvironment` (no extra kwargs needed).
    ``"docker"`` → :class:`DockerEnvironment` (requires ``image``).

    Raises :class:`ValueError` for unknown names.
    """
    cls = _MAPPING.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown environment: {name!r}. "
            f"Choose from: {list(_MAPPING)}"
        )
    return cls(**kwargs)

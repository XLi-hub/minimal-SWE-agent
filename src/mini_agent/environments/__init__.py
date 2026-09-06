"""Environment implementations for the agent.

The package re-exports the stable public API while keeping the core
interface, implementations, and factory in separate modules:

* :mod:`mini_agent.environments.base` — :class:`Environment` and
  :class:`ExecutionResult`.
* :mod:`mini_agent.environments.local` — host-side command execution.
* :mod:`mini_agent.environments.docker` — Docker command execution.
* :mod:`mini_agent.environments.factory` — registry and
  :func:`get_environment`.
"""

from .base import Environment, ExecutionResult
from .docker import DockerEnvironment
from .factory import _MAPPING, get_environment
from .local import LocalEnvironment


__all__ = [
    "Environment",
    "ExecutionResult",
    "LocalEnvironment",
    "DockerEnvironment",
    "get_environment",
    "_MAPPING",
]

"""Factory and registry for built-in execution environments."""

from .base import Environment
from .docker import DockerEnvironment
from .local import LocalEnvironment


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


__all__ = ["_MAPPING", "get_environment"]

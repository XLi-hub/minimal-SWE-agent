"""Tests for the environments subpackage — ABC, factory, and registry."""

import pytest

from src.mini_agent.environments import (
    Environment,
    LocalEnvironment,
    DockerEnvironment,
    get_environment,
    _MAPPING,
)


# ---------------------------------------------------------------------------
# ABC — instantiation prevention
# ---------------------------------------------------------------------------


def test_environment_abc_cannot_be_instantiated():
    """直接实例化 Environment 应抛 TypeError（因为有 @abstractmethod）。"""
    with pytest.raises(TypeError):
        Environment()  # type: ignore[abstract]


def test_local_environment_is_environment():
    """LocalEnvironment 是 Environment 的子类。"""
    assert issubclass(LocalEnvironment, Environment)


def test_docker_environment_is_environment():
    """DockerEnvironment 是 Environment 的子类。"""
    assert issubclass(DockerEnvironment, Environment)


# ---------------------------------------------------------------------------
# factory — get_environment
# ---------------------------------------------------------------------------


def test_get_environment_local_returns_local_environment():
    """get_environment('local') 应返回 LocalEnvironment 实例。"""
    env = get_environment("local")
    assert isinstance(env, LocalEnvironment)


def test_get_environment_docker_returns_docker_environment():
    """get_environment('docker', image=...) 应返回 DockerEnvironment 实例。"""
    # Don't actually start a container — we just patch the __init__
    # so it never calls subprocess.
    import subprocess
    from unittest.mock import patch

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "fake-container-id\n"
        mock_run.return_value.returncode = 0

        env = get_environment("docker", image="python:3.11-slim")
        assert isinstance(env, DockerEnvironment)
        env.cleanup()


def test_get_environment_unknown_raises_value_error():
    """get_environment('unknown') 应抛 ValueError。"""
    with pytest.raises(ValueError, match="Unknown environment"):
        get_environment("kubernetes")


def test_get_environment_error_message_lists_valid_names():
    """错误消息应列出可用的环境名。"""
    with pytest.raises(ValueError) as exc:
        get_environment("podman")
    # The message should mention at least one valid name
    assert "local" in str(exc.value)


def test_get_environment_forwards_kwargs_to_docker():
    """传给 get_environment 的 kwargs 应透传到 DockerEnvironment.__init__。"""
    from unittest.mock import patch

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "fake-id\n"
        mock_run.return_value.returncode = 0

        env = get_environment(
            "docker",
            image="ubuntu:22.04",
            cwd="/workspace",
            timeout=60,
        )

        # Verify kwargs were forwarded to the constructor
        assert env._image == "ubuntu:22.04"
        assert env._cwd == "/workspace"
        assert env._timeout == 60

        env.cleanup()


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_mapping_contains_local_and_docker():
    """注册表应至少包含 local 和 docker 两个入口。"""
    assert "local" in _MAPPING
    assert "docker" in _MAPPING


def test_mapping_values_are_environment_subclasses():
    """注册表的所有 value 都应是 Environment 的子类。"""
    for name, cls in _MAPPING.items():
        assert issubclass(cls, Environment), f"{name} → {cls} is not an Environment subclass"

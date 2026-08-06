"""Tests for DockerEnvironment — unit tests (always run) + integration (skip if no Docker)."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.mini_agent.environments.docker import DockerEnvironment


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_docker_available():
    """Check if Docker daemon is reachable."""
    try:
        subprocess.run(
            ["docker", "version"], capture_output=True, check=True, timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


docker_required = pytest.mark.skipif(
    not _is_docker_available(), reason="Docker not available"
)


# ---------------------------------------------------------------------------
# unit tests — mock subprocess, always run
# ---------------------------------------------------------------------------


def test_start_container_uses_correct_cli_args():
    """Verify docker run is called with the right arguments."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim", cwd="/workspace")

        # cmd is a positional arg, not keyword
        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[0] == "docker"
        assert "run" in cmd
        assert "-d" in cmd
        assert "--rm" in cmd
        assert "python:3.11-slim" in cmd
        assert "sleep" in cmd
        assert "-w" in cmd
        assert "/workspace" in cmd

        env.cleanup()


def test_execute_builds_correct_docker_exec_cmd():
    """Verify docker exec CLI args."""
    with patch("subprocess.run") as mock_run:
        # start call
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim")

        # reset for the execute call
        mock_run.reset_mock()
        mock_run.return_value.stdout = "hello from container\n"
        mock_run.return_value.returncode = 0

        output = env.execute("echo hello")
        cmd = mock_run.call_args.args[0]

        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "-w" in cmd
        assert "/" in cmd  # default cwd
        assert "abc123def" in cmd
        assert "bash" in cmd
        assert "-lc" in cmd
        assert "echo hello" in cmd
        assert "hello from container" in output

        env.cleanup()


def test_execute_passes_env_variables():
    """Environment variables should become -e flags."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(
            image="python:3.11-slim", env={"FOO": "bar", "BAZ": "qux"}
        )

        mock_run.reset_mock()
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        env.execute("echo $FOO")
        cmd = mock_run.call_args.args[0]
        # -e flags should appear before container_id
        assert "-e" in cmd
        assert "FOO=bar" in cmd
        assert "BAZ=qux" in cmd

        env.cleanup()


def test_execute_uses_custom_timeout():
    """Per-call timeout should override the default."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim", timeout=30)

        mock_run.reset_mock()
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0

        env.execute("sleep 100", timeout=5)

        assert mock_run.call_args.kwargs["timeout"] == 5

        env.cleanup()


def test_cleanup_stops_container():
    """cleanup() should call docker stop/rm."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim")
        cid = env._container_id

        mock_run.reset_mock()
        env.cleanup()

        # Should call docker stop/rm via shell
        called_cmd = mock_run.call_args.args[0]
        assert cid in called_cmd
        assert env._container_id is None


def test_execute_raises_when_container_not_started():
    """Calling execute after cleanup should raise."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim")
        env._container_id = None

        with pytest.raises(RuntimeError, match="not been started"):
            env.execute("echo hi")

        env.cleanup()


# ---------------------------------------------------------------------------
# integration tests — only run when Docker is available
# ---------------------------------------------------------------------------


@docker_required
def test_docker_echo():
    """Real container: echo."""
    env = DockerEnvironment(image="python:3.11-slim")
    try:
        output = env.execute("echo 'hello from docker'")
        assert "hello from docker" in output
    finally:
        env.cleanup()


@docker_required
def test_docker_pwd_is_cwd():
    """Real container: pwd should match configured cwd."""
    env = DockerEnvironment(image="python:3.11-slim", cwd="/tmp")
    try:
        output = env.execute("pwd")
        assert "/tmp" in output
    finally:
        env.cleanup()


@docker_required
def test_docker_env_variables():
    """Real container: environment variables are set."""
    env = DockerEnvironment(
        image="python:3.11-slim", env={"MY_VAR": "my_value"}
    )
    try:
        output = env.execute("echo $MY_VAR")
        assert "my_value" in output
    finally:
        env.cleanup()


@docker_required
def test_docker_command_failure():
    """Real container: non-zero exit still returns output."""
    env = DockerEnvironment(image="python:3.11-slim")
    try:
        output = env.execute("bash -c 'echo failing >&2; exit 42'")
        assert "failing" in output
    finally:
        env.cleanup()

"""Tests for DockerEnvironment — unit tests (always run) + integration (skip if no Docker)."""

import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mini_agent.environments.docker import DockerEnvironment


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
docker_integration = pytest.mark.docker


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
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        # start call
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        # execute call — mock Popen + communicate
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("hello from container\n", None)
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim")

        output = env.execute("echo hello")
        cmd = mock_popen.call_args.args[0]

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
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", None)
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(
            image="python:3.11-slim", env={"FOO": "bar", "BAZ": "qux"}
        )

        env.execute("echo $FOO")
        cmd = mock_popen.call_args.args[0]
        # -e flags should appear before container_id
        assert "-e" in cmd
        assert "FOO=bar" in cmd
        assert "BAZ=qux" in cmd

        env.cleanup()


def test_execute_passes_forwarded_env_and_custom_interpreter(monkeypatch):
    """Selected host vars are forwarded and interpreter is configurable."""
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = ("", None)
        mock_popen.return_value.returncode = 0
        monkeypatch.setenv("FORWARDED_TEST_VALUE", "from-host")

        env = DockerEnvironment(
            image="python:3.11-slim",
            forward_env=["FORWARDED_TEST_VALUE"],
            interpreter=["sh", "-c"],
            executable="podman",
            run_args=["--rm", "--network=none"],
            pull_timeout=7,
        )
        env.execute("echo hi")

        start_cmd = mock_run.call_args_list[0].args[0]
        exec_cmd = mock_popen.call_args.args[0]
        assert start_cmd[0] == "podman"
        assert "--network=none" in start_cmd
        assert mock_run.call_args_list[0].kwargs["timeout"] == 7
        assert "FORWARDED_TEST_VALUE=from-host" in exec_cmd
        assert exec_cmd[-3:] == ["sh", "-c", "echo hi"]
        env.cleanup()


def test_execute_returns_structured_result():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("failed\n", None)
        mock_proc.returncode = 42
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim")
        result = env.execute("false")
        assert result == {
            "output": "failed\n",
            "returncode": 42,
            "exception_info": "",
        }
        env.cleanup()


def test_execute_uses_custom_timeout():
    """Per-call timeout should be passed to communicate()."""
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", None)
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim", timeout=30)

        env.execute("sleep 100", timeout=5)

        mock_proc.communicate.assert_called_once_with(timeout=5)

        env.cleanup()


def test_execute_timeout_kills_and_reaps_process_group():
    """A timed-out docker exec must not leave its client process running."""
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen, \
         patch("mini_agent.environments.process.os.killpg") as killpg:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        process = MagicMock(pid=1234)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("docker exec", 0.1, output=b"partial\n"),
            ("partial\n", None),
        ]
        mock_popen.return_value = process

        env = DockerEnvironment(image="python:3.11-slim")
        result = env.execute("sleep 10", timeout=0.1)

        killpg.assert_called_once_with(1234, signal.SIGKILL)
        assert process.communicate.call_count == 2
        assert result["returncode"] == -1
        assert result["output"] == "partial\n"
        assert result["exception_info"].endswith(": sleep 10")
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


def test_read_file_builds_correct_cmd():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("file contents\n", None)
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim", cwd="/workspace")
        out = env.read_file("src/app.py")

        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "-w" in cmd
        assert "/workspace" in cmd
        assert "cat" in cmd
        assert "--" in cmd
        assert "src/app.py" in cmd
        assert out == "file contents\n"

        env.cleanup()


def test_read_file_missing_raises_file_not_found():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            "cat: nope: No such file or directory\n", None
        )
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim")
        with pytest.raises(FileNotFoundError):
            env.read_file("nope")

        env.cleanup()


def test_read_file_timeout_kills_and_reaps_process_group():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen, \
         patch("mini_agent.environments.process.os.killpg") as killpg:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        process = MagicMock(pid=1234)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("docker exec cat", 30),
            ("", None),
        ]
        mock_popen.return_value = process

        env = DockerEnvironment(image="python:3.11-slim")
        with pytest.raises(subprocess.TimeoutExpired):
            env.read_file("slow.txt")

        killpg.assert_called_once_with(1234, signal.SIGKILL)
        assert process.communicate.call_count == 2
        env.cleanup()


def test_write_file_passes_content_on_stdin():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", None)
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        env = DockerEnvironment(image="python:3.11-slim", cwd="/workspace")
        env.write_file("src/app.py", "print('hi')\n")

        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "-i" in cmd
        assert "-w" in cmd
        assert "/workspace" in cmd
        assert "sh" in cmd
        assert "-c" in cmd
        mock_proc.communicate.assert_called_once_with(
            input="print('hi')\n", timeout=30
        )

        env.cleanup()


def test_write_file_timeout_kills_and_reaps_process_group():
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.Popen") as mock_popen, \
         patch("mini_agent.environments.process.os.killpg") as killpg:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        process = MagicMock(pid=1234)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("docker exec write", 30),
            ("", None),
        ]
        mock_popen.return_value = process

        env = DockerEnvironment(image="python:3.11-slim")
        with pytest.raises(subprocess.TimeoutExpired):
            env.write_file("slow.txt", "content")

        killpg.assert_called_once_with(1234, signal.SIGKILL)
        assert process.communicate.call_count == 2
        env.cleanup()


def test_read_write_file_raise_when_not_started():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.stdout = "abc123def\n"
        mock_run.return_value.returncode = 0

        env = DockerEnvironment(image="python:3.11-slim")
        env._container_id = None

        with pytest.raises(RuntimeError, match="not been started"):
            env.read_file("x")
        with pytest.raises(RuntimeError, match="not been started"):
            env.write_file("x", "y")

        env.cleanup()


# ---------------------------------------------------------------------------
# integration tests — only run when Docker is available
# ---------------------------------------------------------------------------


@docker_integration
@docker_required
def test_docker_echo():
    """Real container: echo."""
    env = DockerEnvironment(image="python:3.11-slim")
    try:
        output = env.execute("echo 'hello from docker'")
        assert "hello from docker" in output
    finally:
        env.cleanup()


@docker_integration
@docker_required
def test_docker_pwd_is_cwd():
    """Real container: pwd should match configured cwd."""
    env = DockerEnvironment(image="python:3.11-slim", cwd="/tmp")
    try:
        output = env.execute("pwd")
        assert "/tmp" in output
    finally:
        env.cleanup()


@docker_integration
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


@docker_integration
@docker_required
def test_docker_command_failure():
    """Real container: non-zero exit still returns output."""
    env = DockerEnvironment(image="python:3.11-slim")
    try:
        output = env.execute("bash -c 'echo failing >&2; exit 42'")
        assert "failing" in output
    finally:
        env.cleanup()

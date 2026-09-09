import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mini_agent.config import EnvironmentConfig
from mini_agent.environments.local import LocalEnvironment


def test_echo():
    output = LocalEnvironment().execute("echo hello world")
    assert "hello world" in output
    assert output["output"] == "hello world\n"
    assert output["returncode"] == 0
    assert output["exception_info"] == ""


def test_pwd():
    output = LocalEnvironment().execute("pwd")
    assert output.strip() != ""


def test_ls():
    output = LocalEnvironment().execute("ls /")
    assert len(output) > 0


def test_captures_stderr():
    output = LocalEnvironment().execute("bash -c 'echo error msg >&2; exit 1'")
    assert "error msg" in output
    assert output["returncode"] == 1
    assert output["exception_info"] == ""


def test_nonexistent_command():
    output = LocalEnvironment().execute("nonexistent_command_xyz 2>&1")
    assert len(output) > 0


def test_env_overrides_pager():
    """PAGER should be overridden to cat to prevent interactive pagers."""
    output = LocalEnvironment().execute("echo $PAGER")
    assert "cat" in output


def test_env_overrides_tqdm():
    """TQDM_DISABLE should be set to 1."""
    output = LocalEnvironment().execute("echo $TQDM_DISABLE")
    assert "1" in output


def test_local_environment_hides_protected_host_variable(monkeypatch):
    monkeypatch.setenv("MINI_AGENT_TEST_SECRET", "sentinel")
    config = EnvironmentConfig(protected_env=["MINI_AGENT_TEST_SECRET"])

    result = LocalEnvironment(config).execute(
        'printf "%s" "${MINI_AGENT_TEST_SECRET-missing}"'
    )

    assert result["output"] == "missing"


def test_local_environment_requires_explicit_forward_for_protected_variable(monkeypatch):
    monkeypatch.setenv("MINI_AGENT_TEST_SECRET", "sentinel")
    config = EnvironmentConfig(
        protected_env=["MINI_AGENT_TEST_SECRET"],
        forward_env=["MINI_AGENT_TEST_SECRET"],
    )

    result = LocalEnvironment(config).execute('printf "%s" "$MINI_AGENT_TEST_SECRET"')

    assert result["output"] == "sentinel"


def test_local_environment_does_not_reinject_protected_config_value():
    config = EnvironmentConfig(
        env={"MINI_AGENT_TEST_SECRET": "sentinel"},
        protected_env=["MINI_AGENT_TEST_SECRET"],
    )

    result = LocalEnvironment(config).execute(
        'printf "%s" "${MINI_AGENT_TEST_SECRET-missing}"'
    )

    assert result["output"] == "missing"


# --- LocalEnvironment class interface ---


def test_local_environment_basic_execution():
    env = LocalEnvironment()
    output = env.execute("echo hello docker")
    assert "hello docker" in output


def test_local_environment_timeout():
    env = LocalEnvironment()
    result = env.execute("sleep 10", timeout=0.1)
    assert result["returncode"] == -1
    assert "timed out" in result["exception_info"]


def test_windows_local_timeout_terminates_process_tree():
    process = MagicMock(pid=1234)
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("sleep 10", 0.1),
        ("", None),
    ]

    with patch("mini_agent.environments.local.os.name", "nt"), \
         patch("mini_agent.environments.local.subprocess.Popen", return_value=process), \
         patch("mini_agent.environments.local.subprocess.run") as run:
        result = LocalEnvironment().execute("sleep 10", timeout=0.1)

    run.assert_called_once_with(
        ["taskkill", "/PID", "1234", "/T", "/F"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    process.kill.assert_not_called()
    assert result["returncode"] == -1


# --- read_file / write_file ---


def test_read_file_returns_content(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    assert LocalEnvironment().read_file(str(p)) == "hello\nworld\n"


def test_read_file_unicode_roundtrip(tmp_path):
    p = tmp_path / "u.txt"
    p.write_text("你好，世界\n", encoding="utf-8")
    assert LocalEnvironment().read_file(str(p)) == "你好，世界\n"


def test_read_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalEnvironment().read_file(str(tmp_path / "does_not_exist.txt"))


def test_write_file_writes_content(tmp_path):
    p = tmp_path / "out.txt"
    LocalEnvironment().write_file(str(p), "abc")
    assert p.read_text(encoding="utf-8") == "abc"


def test_write_file_creates_parent_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "c.txt"
    LocalEnvironment().write_file(str(p), "x")
    assert p.read_text(encoding="utf-8") == "x"


def test_write_file_overwrites(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("old", encoding="utf-8")
    LocalEnvironment().write_file(str(p), "new")
    assert p.read_text(encoding="utf-8") == "new"

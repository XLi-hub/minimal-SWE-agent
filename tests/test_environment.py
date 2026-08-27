import signal
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

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


def test_local_environment_timeout_kills_and_reaps_process_group():
    process = MagicMock(pid=1234)
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("sleep 10", 0.1, output=b"partial\n"),
        ("partial\n", None),
    ]

    with patch("subprocess.Popen", return_value=process), \
         patch("mini_agent.environments.process.os.killpg") as killpg:
        result = LocalEnvironment().execute("sleep 10", timeout=0.1)

    killpg.assert_called_once_with(1234, signal.SIGKILL)
    assert process.communicate.call_args_list == [call(timeout=0.1), call()]
    assert result["returncode"] == -1
    assert result["output"] == "partial\n"


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

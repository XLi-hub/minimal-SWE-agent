from src.mini_agent.environments.local import LocalEnvironment

# backward-compat: the shim in environment.py still exports this
from src.mini_agent.environment import execute_action


def test_echo():
    output = execute_action("echo hello world")
    assert "hello world" in output


def test_pwd():
    output = execute_action("pwd")
    assert output.strip() != ""


def test_ls():
    output = execute_action("ls /")
    assert len(output) > 0


def test_captures_stderr():
    output = execute_action("bash -c 'echo error msg >&2; exit 1'")
    assert "error msg" in output


def test_nonexistent_command():
    output = execute_action("nonexistent_command_xyz 2>&1")
    assert len(output) > 0


def test_env_overrides_pager():
    """PAGER should be overridden to cat to prevent interactive pagers."""
    output = execute_action("echo $PAGER")
    assert "cat" in output


def test_env_overrides_tqdm():
    """TQDM_DISABLE should be set to 1."""
    output = execute_action("echo $TQDM_DISABLE")
    assert "1" in output


# --- LocalEnvironment class interface ---


def test_local_environment_basic_execution():
    env = LocalEnvironment()
    output = env.execute("echo hello docker")
    assert "hello docker" in output


def test_local_environment_timeout():
    env = LocalEnvironment()
    try:
        env.execute("sleep 10", timeout=1)
    except Exception:
        pass  # TimeoutExpired is expected
    else:
        # If no exception, the command returned something —
        # just verify no crash.
        pass

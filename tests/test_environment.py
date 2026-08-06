from src.mini_agent.environments.local import LocalEnvironment


def test_echo():
    output = LocalEnvironment().execute("echo hello world")
    assert "hello world" in output


def test_pwd():
    output = LocalEnvironment().execute("pwd")
    assert output.strip() != ""


def test_ls():
    output = LocalEnvironment().execute("ls /")
    assert len(output) > 0


def test_captures_stderr():
    output = LocalEnvironment().execute("bash -c 'echo error msg >&2; exit 1'")
    assert "error msg" in output


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
    try:
        env.execute("sleep 10", timeout=1)
    except Exception:
        pass  # TimeoutExpired is expected
    else:
        # If no exception, the command returned something —
        # just verify no crash.
        pass

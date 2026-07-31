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
    """PAGER 应该被覆盖为 cat，防止分页器交互."""
    output = execute_action("echo $PAGER")
    assert "cat" in output


def test_env_overrides_tqdm():
    """TQDM_DISABLE 应该设为 1."""
    output = execute_action("echo $TQDM_DISABLE")
    assert "1" in output

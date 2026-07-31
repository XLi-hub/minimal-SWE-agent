from unittest.mock import patch
from src.mini_agent.agent import run


def test_exits_on_exit_action():
    """LM 返回 exit 时 agent 应跳出循环。"""
    mock_responses = [
        "I'll exit now.\n\n```bash-action\nexit\n```",
    ]

    with (
        patch("src.mini_agent.agent.query_lm", side_effect=mock_responses),
        patch("src.mini_agent.agent.execute_action", return_value="done"),
    ):
        messages = run("test task")

    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant"]


def test_executes_command_then_exit():
    """Agent 先执行命令，再收到 exit 退出。"""
    mock_responses = [
        "Let me list files.\n\n```bash-action\nls\n```",
        "Files listed. Exiting.\n\n```bash-action\nexit\n```",
    ]
    commands: list[str] = []

    def fake_execute(cmd):
        commands.append(cmd)
        return f"output of: {cmd}"

    with (
        patch("src.mini_agent.agent.query_lm", side_effect=mock_responses),
        patch("src.mini_agent.agent.execute_action", wraps=fake_execute),
    ):
        run("list files")

    assert commands == ["ls"]

def test_recovers_from_execution_error():
    """execute_action 抛出异常时，agent应该把错误喂给 LM 而不是崩溃"""
    mock_responses = [
        "I'll run a risky command.\n\n```bash-action\nrisky_cmd\n```",
        "OK, let me exit.\n\n```bash-action\nexit\n```",
    ]

    def fake_execute(cmd):
        raise RuntimeError("something went wrong")

    with (
        patch("src.mini_agent.agent.query_lm", side_effect=mock_responses),
        patch("src.mini_agent.agent.execute_action", wraps=fake_execute),
    ):
        messages = run("test task")  

    # 验证：错误信息被添加到了 messages 里
    errors = [m for m in messages if m["role"] == "user" and "something went wrong" in m["content"]]
    assert len(errors) == 1
    # 验证：agent 没有崩溃，正常结束
    assert messages[-1]["role"] == "assistant" 
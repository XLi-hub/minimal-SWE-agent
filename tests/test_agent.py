from unittest.mock import MagicMock
from src.mini_agent.agent import Agent


def test_exits_on_exit_action():
    """LM 返回 exit 时 agent 应跳出循环。"""
    model = MagicMock()
    model.query.return_value = "I'll exit now.\n\n```bash-action\nexit\n```"
    env = MagicMock()

    agent = Agent(model, env)
    messages = agent.run("test task")

    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant"]


def test_executes_command_then_exit():
    """Agent 先执行命令，再收到 exit 退出。"""
    model = MagicMock()
    model.query.side_effect = [
        "Let me list files.\n\n```bash-action\nls\n```",
        "Files listed. Exiting.\n\n```bash-action\nexit\n```",
    ]
    commands: list[str] = []

    def fake_execute(cmd):
        commands.append(cmd)
        return f"output of: {cmd}"

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    agent.run("list files")

    assert commands == ["ls"]


def test_recovers_from_execution_error():
    """execute_action 抛异常时，agent 应把错误喂回 LM 而不是崩溃。"""
    model = MagicMock()
    model.query.side_effect = [
        "I'll run a risky command.\n\n```bash-action\nrisky_cmd\n```",
        "OK, let me exit.\n\n```bash-action\nexit\n```",
    ]

    def fake_execute(cmd):
        raise RuntimeError("something went wrong")

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    messages = agent.run("test task")

    # 验证：错误信息被添加到了 messages 里
    errors = [m for m in messages if "something went wrong" in m.get("content", "")]
    assert len(errors) >= 1
    # 验证：agent 没有崩溃，正常结束
    assert messages[-1]["role"] == "assistant"

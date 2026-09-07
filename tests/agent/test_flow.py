from unittest.mock import MagicMock

from mini_agent.agent import Agent

from ._helpers import DEFAULTS, _make_response, _make_tool_call


# --- basic flow ---


def test_exits_when_no_tool_calls():
    """模型返回纯文本（无工具调用）时 agent 应退出循环。"""
    model = MagicMock()
    model.query.return_value = _make_response(
        content="Task is done, no more commands needed.",
    )
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("test task")

    assert result["exit_status"] == "no_tool_calls"
    assert result["submission"] == ""
    roles = [m["role"] for m in result["messages"]]
    assert roles == ["system", "user", "assistant"]


def test_executes_tool_call_then_exits():
    """Agent 先执行工具的 bash 命令，模型再返回纯文本退出。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me list files.",
            tool_calls=[_make_tool_call("call_1", "bash", {"command": "ls"})],
        ),
        _make_response(
            content="Files listed. Done.",
        ),
    ]
    commands: list[str] = []

    def fake_execute(cmd, **kwargs):
        commands.append(cmd)
        return f"output of: {cmd}"

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    result = agent.run("list files")

    assert commands == ["ls"]
    assert result["exit_status"] == "no_tool_calls"


def test_recovers_from_execution_error():
    """工具执行抛异常时，agent 应把错误发回 LM 而不是崩溃。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="I'll run a risky command.",
            tool_calls=[
                _make_tool_call("call_1", "bash", {"command": "risky_cmd"})
            ],
        ),
        _make_response(content="OK, error handled. Done."),
    ]

    def fake_execute(cmd, **kwargs):
        raise RuntimeError("something went wrong")

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    result = agent.run("test task")

    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert "something went wrong" in tool_msgs[0].get("content", "")

    assert result["exit_status"] == "no_tool_calls"


def test_unknown_tool_call_returns_error_and_continues():
    """未知工具应收到 tool error，agent 随后仍可继续下一轮。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content=None,
            tool_calls=[
                _make_tool_call("call_1", "other_tool", {"key": "val"}),
            ],
        ),
        _make_response(content="Done."),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("test")
    assert result["exit_status"] == "no_tool_calls"
    tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
    assert "unknown tool" in tool_messages[0]["content"]


def test_multiple_tool_calls_in_one_response():
    """单次 LM 回复可含多个工具调用，agent 应全部执行。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="I'll run two commands.",
            tool_calls=[
                _make_tool_call("c1", "bash", {"command": "cmd1"}),
                _make_tool_call("c2", "bash", {"command": "cmd2"}),
            ],
        ),
        _make_response(content="Both done."),
    ]
    commands: list[str] = []

    def fake_execute(cmd, **kwargs):
        commands.append(cmd)
        return f"ok: {cmd}"

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    agent.run("run two commands")

    assert commands == ["cmd1", "cmd2"]

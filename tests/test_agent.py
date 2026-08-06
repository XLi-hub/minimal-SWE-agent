import json
from unittest.mock import MagicMock
from src.mini_agent.agent import Agent, _truncate_output


# --- helpers ---

def _make_response(content=None, tool_calls=None):
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = msg

    response = MagicMock()
    response.choices = [choice]
    return response


def _make_tool_call(id_: str, name: str, arguments: dict):
    """Build a mock tool call object (minimal OpenAI shape)."""
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


# --- tests ---

def test_exits_when_no_tool_calls():
    """模型返回纯文本（无工具调用）时 agent 应退出循环。"""
    model = MagicMock()
    model.query.return_value = _make_response(
        content="Task is done, no more commands needed.",
    )
    env = MagicMock()

    agent = Agent(model, env)
    messages = agent.run("test task")

    # 应只有 system, user, assistant 三条消息
    roles = [m["role"] for m in messages]
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

    def fake_execute(cmd):
        commands.append(cmd)
        return f"output of: {cmd}"

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    agent.run("list files")

    assert commands == ["ls"]


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

    def fake_execute(cmd):
        raise RuntimeError("something went wrong")

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    messages = agent.run("test task")

    # 验证：错误信息被添加到了 messages 里
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert "something went wrong" in tool_msgs[0].get("content", "")

    # 验证：agent 没有崩溃，正常结束
    assert messages[-1]["role"] == "assistant"


def test_ignores_non_bash_tool_calls():
    """非 bash 的工具调用应被忽略（不抛异常）。"""
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
    messages = agent.run("test")
    assert messages[-1]["role"] == "assistant"


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

    def fake_execute(cmd):
        commands.append(cmd)
        return f"ok: {cmd}"

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    agent.run("run two commands")

    assert commands == ["cmd1", "cmd2"]


# --- truncation ---


class TestTruncateOutput:
    """Tests for _truncate_output."""

    def test_short_output_passes_through(self):
        output = "line 1\nline 2\nline 3"
        assert _truncate_output(output, max_lines=10) == output

    def test_exactly_at_limit_passes_through(self):
        output = "\n".join(str(i) for i in range(10))
        assert _truncate_output(output, max_lines=10) == output

    def test_long_output_is_truncated(self):
        lines = [f"line {i}" for i in range(200)]
        output = "\n".join(lines)
        result = _truncate_output(output, max_lines=100)
        # Split back and check structure
        result_lines = result.splitlines()
        # head (50) + elision marker (1) + tail (50) = 101
        assert len(result_lines) == 101
        assert result_lines[0] == "line 0"
        assert result_lines[-1] == "line 199"
        # elision marker in the middle
        assert any("100 lines truncated" in line for line in result_lines)

    def test_truncation_includes_elision_info(self):
        lines = [f"L{i:04d}" for i in range(500)]
        output = "\n".join(lines)
        result = _truncate_output(output, max_lines=50)
        assert "450 lines truncated" in result
        assert "500 total" in result
        assert "50 shown" in result

    def test_minimum_lines_is_2(self):
        output = "\n".join(str(i) for i in range(10))
        result = _truncate_output(output, max_lines=1)  # clamped to 2
        result_lines = result.splitlines()
        # head(1) + elision(1) + tail(1) = 3
        assert len(result_lines) == 3
        assert "8 lines truncated" in result

    def test_lines_zero_is_clamped(self):
        output = "a\nb\nc\nd\ne"
        result = _truncate_output(output, max_lines=0)  # clamped to 2
        result_lines = result.splitlines()
        assert len(result_lines) == 3  # 1 head + 1 marker + 1 tail
        assert result_lines[0] == "a"
        assert result_lines[-1] == "e"


class TestAgentTruncation:
    """Tests that Agent uses truncation with the 'lines' parameter."""

    def test_default_truncation_applied(self):
        """不传 lines 时使用默认 100 行截断。"""
        long_output = "\n".join(f"line {i}" for i in range(300))
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Running.",
                tool_calls=[
                    _make_tool_call("c1", "bash", {"command": "cat big.txt"}),
                ],
            ),
            _make_response(content="Seen enough. Done."),
        ]
        env = MagicMock()
        env.execute.return_value = long_output

        agent = Agent(model, env)
        messages = agent.run("show big file")

        tool_msg = [m for m in messages if m["role"] == "tool"][0]
        assert "lines truncated" in tool_msg["content"]
        # 200 lines were elided (300 - 100 default)
        assert "200 lines truncated" in tool_msg["content"]

    def test_custom_lines_from_tool_call(self):
        """模型传了 lines=10，应按 10 行截断。"""
        long_output = "\n".join(f"line {i}" for i in range(100))
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Let me check.",
                tool_calls=[
                    _make_tool_call(
                        "c1", "bash", {"command": "cat", "lines": 10}
                    ),
                ],
            ),
            _make_response(content="OK, done."),
        ]
        env = MagicMock()
        env.execute.return_value = long_output

        agent = Agent(model, env)
        messages = agent.run("read file")

        tool_msg = [m for m in messages if m["role"] == "tool"][0]
        assert "90 lines truncated" in tool_msg["content"]
        assert "10 shown" in tool_msg["content"]

    def test_short_output_not_truncated(self):
        """输出很短时不应截断，也不应有 elision 标记。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content=None,
                tool_calls=[
                    _make_tool_call("c1", "bash", {"command": "echo hi"}),
                ],
            ),
            _make_response(content="Done."),
        ]
        env = MagicMock()
        env.execute.return_value = "hello"

        agent = Agent(model, env)
        messages = agent.run("say hi")

        tool_msg = [m for m in messages if m["role"] == "tool"][0]
        assert tool_msg["content"] == "hello"
        assert "truncated" not in tool_msg["content"]

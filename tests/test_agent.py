import json
from unittest.mock import MagicMock

from src.mini_agent.agent import Agent, _format_assistant_message, _truncate_output


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

    def fake_execute(cmd):
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

    def fake_execute(cmd):
        raise RuntimeError("something went wrong")

    env = MagicMock()
    env.execute.side_effect = fake_execute

    agent = Agent(model, env)
    result = agent.run("test task")

    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) >= 1
    assert "something went wrong" in tool_msgs[0].get("content", "")

    assert result["exit_status"] == "no_tool_calls"


def test_ignores_non_bash_tool_calls():
    """非 bash/submit 的工具调用应被忽略（不抛异常）。"""
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


# --- submit ---


def test_submit_exits_with_submission():
    """模型调用 submit 工具时应退出并返回提交内容。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="I am done.",
            tool_calls=[
                _make_tool_call(
                    "s1", "submit",
                    {"output": "Fixed the bug in utils.py line 42."},
                ),
            ],
        ),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("fix a bug")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "Fixed the bug in utils.py line 42."


def test_submit_tool_result_is_recorded():
    """submit 调用应被记录为 tool 消息。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content=None,
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "patch content"}),
            ],
        ),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("submit a patch")

    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "Submitted."


def test_submit_stops_loop_immediately():
    """submit 之后不应有后续 model.query 调用。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Submitting.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "done"}),
            ],
        ),
        # This should never be reached
        _make_response(content="This should not happen."),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("do something and submit")

    assert result["exit_status"] == "submitted"
    assert model.query.call_count == 1


def test_submit_with_patch():
    """SWE-bench 风格：模型生成 git diff patch 后提交。"""
    patch = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-print('buggy')\n"
        "+print('fixed')\n"
    )
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Here is the fix.",
            tool_calls=[_make_tool_call("s1", "submit", {"output": patch})],
        ),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("fix the bug and submit a patch")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == patch


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
        result_lines = result.splitlines()
        assert len(result_lines) == 101
        assert result_lines[0] == "line 0"
        assert result_lines[-1] == "line 199"
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
        result = _truncate_output(output, max_lines=1)
        result_lines = result.splitlines()
        assert len(result_lines) == 3
        assert "8 lines truncated" in result

    def test_lines_zero_is_clamped(self):
        output = "a\nb\nc\nd\ne"
        result = _truncate_output(output, max_lines=0)
        result_lines = result.splitlines()
        assert len(result_lines) == 3
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
        result = agent.run("show big file")

        tool_msg = [m for m in result["messages"] if m["role"] == "tool"][0]
        assert "lines truncated" in tool_msg["content"]
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
        result = agent.run("read file")

        tool_msg = [m for m in result["messages"] if m["role"] == "tool"][0]
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
        result = agent.run("say hi")

        tool_msg = [m for m in result["messages"] if m["role"] == "tool"][0]
        assert tool_msg["content"] == "hello"
        assert "truncated" not in tool_msg["content"]


# --- tools list ---


def test_agent_passes_both_bash_and_submit_tools():
    """Agent 应传递 BASH_TOOL 和 SUBMIT_TOOL 给模型。"""
    model = MagicMock()
    model.query.return_value = _make_response(
        content="Done.",
        tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
    )
    env = MagicMock()

    agent = Agent(model, env)
    agent.run("test")

    call_args = model.query.call_args
    tools = call_args.kwargs["tools"]
    tool_names = [t["function"]["name"] for t in tools]
    assert "bash" in tool_names
    assert "submit" in tool_names


# --- keyboard interrupt ---


def test_keyboard_interrupt_returns_interrupted_status():
    """用户 Ctrl+C 时应返回 exit_status='interrupted'。"""
    model = MagicMock()
    model.query.side_effect = KeyboardInterrupt()
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("do something")

    assert result["exit_status"] == "interrupted"
    assert result["submission"] == ""


def test_keyboard_interrupt_preserves_messages_so_far():
    """中断时应保留中断前已有的消息历史。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Let me check.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
        ),
        KeyboardInterrupt(),  # 第二轮 Ctrl+C
    ]
    env = MagicMock()
    env.execute.return_value = "file1 file2"

    agent = Agent(model, env)
    result = agent.run("list files")

    assert result["exit_status"] == "interrupted"
    # 至少应该有 system + user + assistant(with tool_call) + tool
    roles = [m["role"] for m in result["messages"]]
    assert "tool" in roles


# --- model error recovery ---


def test_model_query_exception_is_appended_as_user_message():
    """model.query 抛普通异常时（如网络错误），agent 应把错误
    作为 user 消息追加并继续循环，而不是崩溃。"""
    model = MagicMock()
    model.query.side_effect = [
        RuntimeError("network timeout"),
        _make_response(content="OK, recovered."),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("test")

    # 错误被追加为 user 消息
    user_msgs = [m for m in result["messages"] if m["role"] == "user"]
    error_msg = [m for m in user_msgs if "network timeout" in str(m["content"])]
    assert len(error_msg) == 1

    # 循环继续，最终正常退出
    assert result["exit_status"] == "no_tool_calls"
    assert model.query.call_count == 2


# --- multi-tool-call with submit ---


def test_submit_and_bash_in_same_response_stops_immediately():
    """同一轮同时有 submit 和 bash → submit 退出，后续 bash 不执行。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Done with everything.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "final answer"}),
                _make_tool_call("c1", "bash", {"command": "rm -rf /"}),
            ],
        ),
    ]
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("do it and submit")

    # submit 优先，bash 不应该被执行
    env.execute.assert_not_called()
    assert result["exit_status"] == "submitted"
    assert result["submission"] == "final answer"


# --- _format_assistant_message ---


def test_format_assistant_message_with_tool_calls():
    """验证 _format_assistant_message 输出正确的 dict 结构。"""
    msg = MagicMock()
    msg.content = "I will run a command."

    tc = MagicMock()
    tc.id = "call_42"
    tc.function.name = "bash"
    tc.function.arguments = '{"command": "ls"}'
    msg.tool_calls = [tc]

    result = _format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "I will run a command."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "call_42"
    assert result["tool_calls"][0]["type"] == "function"
    assert result["tool_calls"][0]["function"]["name"] == "bash"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def test_format_assistant_message_without_tool_calls():
    """无 tool_calls 时应返回空列表。"""
    msg = MagicMock()
    msg.content = "Hello."
    msg.tool_calls = []

    result = _format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "Hello."
    assert result["tool_calls"] == []


# --- module-level run() convenience function ---


def test_module_level_run_uses_default_agent():
    """向后兼容的 run(task) 应返回 dict，不抛异常。"""
    import src.mini_agent.agent as agent_module

    # Set up a mock agent so we don't need real Model/Environment
    mock_agent = MagicMock()
    mock_agent.run.return_value = {
        "exit_status": "submitted",
        "submission": "ok",
        "messages": [],
    }
    agent_module._default_agent = mock_agent

    result = agent_module.run("test task")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "ok"
    mock_agent.run.assert_called_once_with("test task")

    # Clean up — reset global so other tests aren't affected
    agent_module._default_agent = None

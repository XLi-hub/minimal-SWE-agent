import json
import subprocess
from unittest.mock import MagicMock

from mini_agent.agent import Agent
from mini_agent.config import get_default_config
from mini_agent.tools import (
    decode_timeout_output,
    format_assistant_message,
    truncate_output,
)

DEFAULTS = get_default_config()
SUMMARY_MARKER = DEFAULTS.agent.summary_marker


# --- helpers ---

def _make_response(content=None, tool_calls=None, usage=None):
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = msg

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_usage(prompt_tokens, completion_tokens, cached_tokens=None):
    """Build a mock OpenAI usage object (token counts)."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    if cached_tokens is not None:
        usage.prompt_tokens_details.cached_tokens = cached_tokens
    return usage


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
    """Tests for truncate_output."""

    def test_short_output_passes_through(self):
        output = "line 1\nline 2\nline 3"
        assert truncate_output(output, max_lines=10) == output

    def test_exactly_at_limit_passes_through(self):
        output = "\n".join(str(i) for i in range(10))
        assert truncate_output(output, max_lines=10) == output

    def test_long_output_is_truncated(self):
        lines = [f"line {i}" for i in range(200)]
        output = "\n".join(lines)
        result = truncate_output(output, max_lines=100)
        result_lines = result.splitlines()
        assert len(result_lines) == 102  # 50 head + 2 (marker+warning) + 50 tail
        assert result_lines[0] == "line 0"
        assert result_lines[-1] == "line 199"
        assert any("100 lines truncated" in line for line in result_lines)

    def test_truncation_includes_elision_info(self):
        lines = [f"L{i:04d}" for i in range(500)]
        output = "\n".join(lines)
        result = truncate_output(output, max_lines=50)
        assert "450 lines truncated" in result
        assert "500 total" in result
        assert "50 shown" in result

    def test_minimum_lines_is_2(self):
        output = "\n".join(str(i) for i in range(10))
        result = truncate_output(output, max_lines=1)
        result_lines = result.splitlines()
        assert len(result_lines) == 4  # 1 head + 2 (marker+warning) + 1 tail
        assert "8 lines truncated" in result

    def test_lines_zero_is_clamped(self):
        output = "a\nb\nc\nd\ne"
        result = truncate_output(output, max_lines=0)
        result_lines = result.splitlines()
        assert len(result_lines) == 4  # 1 head + 2 (marker+warning) + 1 tail
        assert result_lines[0] == "a"
        assert result_lines[-1] == "e"
        assert "WARNING" in result


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


def test_agent_passes_file_tools_by_default():
    """默认配置（含 read/edit/write）时，五个工具都应传给模型。"""
    model = MagicMock()
    model.query.return_value = _make_response(
        content="Done.",
        tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
    )
    env = MagicMock()

    agent = Agent(model, env)  # 默认 = default.yaml → 5 工具
    agent.run("test")

    tools = model.query.call_args.kwargs["tools"]
    tool_names = [t["function"]["name"] for t in tools]
    assert tool_names == ["bash", "submit", "read", "edit", "write"]


def test_agent_bash_only_config_passes_only_bash_and_submit():
    """--config default_bash 时，应只有 bash + submit 两个工具。"""
    from mini_agent.config import build_config

    cfg = build_config(["default_bash"])
    model = MagicMock()
    model.query.return_value = _make_response(
        content="Done.",
        tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
    )
    env = MagicMock()

    agent = Agent(model, env, config=cfg)
    agent.run("test")

    tools = model.query.call_args.kwargs["tools"]
    tool_names = [t["function"]["name"] for t in tools]
    assert tool_names == ["bash", "submit"]


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


# --- format_assistant_message ---


def testformat_assistant_message_with_tool_calls():
    """验证 format_assistant_message 输出正确的 dict 结构。"""
    msg = MagicMock()
    msg.content = "I will run a command."

    tc = MagicMock()
    tc.id = "call_42"
    tc.function.name = "bash"
    tc.function.arguments = '{"command": "ls"}'
    msg.tool_calls = [tc]

    result = format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "I will run a command."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "call_42"
    assert result["tool_calls"][0]["type"] == "function"
    assert result["tool_calls"][0]["function"]["name"] == "bash"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"command": "ls"}'


def testformat_assistant_message_without_tool_calls():
    """无 tool_calls 时应返回空列表。"""
    msg = MagicMock()
    msg.content = "Hello."
    msg.tool_calls = []

    result = format_assistant_message(msg)

    assert result["role"] == "assistant"
    assert result["content"] == "Hello."
    assert result["tool_calls"] == []


# --- module-level run() convenience function ---


def test_module_level_run_uses_default_agent():
    """向后兼容的 run(task) 应返回 dict，不抛异常。"""
    import mini_agent.agent as agent_module

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

    mock_agent.run.assert_called_once_with(
        "test task", max_steps=DEFAULTS.agent.max_steps,
        max_time=DEFAULTS.agent.max_time,
        cost_limit=DEFAULTS.agent.cost_limit,
    )

    # Clean up — reset global so other tests aren't affected
    agent_module._default_agent = None


# --- max_steps ---


def test_exits_with_max_steps_when_limit_reached():
    """达到 max_steps 时返回 exit_status='max_steps'，不崩溃。"""
    model = MagicMock()
    # Every query returns a bash tool call — the agent will keep going
    model.query.return_value = _make_response(
        content="Running more commands.",
        tool_calls=[_make_tool_call("c1", "bash", {"command": "echo loop"})],
    )
    env = MagicMock()
    env.execute.return_value = "ok"

    agent = Agent(model, env)
    result = agent.run("infinite task", max_steps=3)

    assert result["exit_status"] == "max_steps"
    assert model.query.call_count == 3
    # Messages should be preserved so the user can inspect what happened
    assert len(result["messages"]) > 0


def test_max_steps_default_is_applied_when_not_specified():
    """不传 max_steps 时使用默认值，不会无限循环。"""
    model = MagicMock()
    # Make the model always call bash, so it would loop forever
    # without the default max_steps limit
    model.query.return_value = _make_response(
        content="Running.",
        tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
    )
    env = MagicMock()
    env.execute.return_value = "output"

    agent = Agent(model, env)
    result = agent.run("never ending task")

    # Should exit with max_steps after the default max_steps iterations
    assert result["exit_status"] == "max_steps"
    assert model.query.call_count == DEFAULTS.agent.max_steps


# --- timeout forwarding ---


def test_default_timeout_passed_to_execute():
    """不传 timeout 时使用默认值传给 environment.execute()。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Running.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
        ),
        _make_response(content="Done."),
    ]
    env = MagicMock()
    env.execute.return_value = "output"

    agent = Agent(model, env)

    agent.run("list files", max_steps=5)

    env.execute.assert_called_once_with("ls", timeout=DEFAULTS.tools.default_timeout)


def test_custom_timeout_from_tool_call():
    """模型传了 timeout=120→execute() 应收到 timeout=120。"""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Installing dependencies.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash",
                    {"command": "pip install torch", "timeout": 120},
                ),
            ],
        ),
        _make_response(content="Done."),
    ]
    env = MagicMock()
    env.execute.return_value = "installed"

    agent = Agent(model, env)
    agent.run("install torch", max_steps=5)

    env.execute.assert_called_once_with("pip install torch", timeout=120)


# ---------------------------------------------------------------------------
# decode_timeout_output
# ---------------------------------------------------------------------------


class TestDecodeTimeoutOutput:
    """Tests for decode_timeout_output."""

    def test_decodes_bytes_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = b"partial output line 1\npartial line 2\n"
        result = decode_timeout_output(exc)
        assert "partial output line 1" in result
        assert "partial line 2" in result

    def test_handles_none_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = None
        result = decode_timeout_output(exc)
        assert "no output before timeout" in result.lower()

    def test_passes_through_string_stdout(self):
        exc = subprocess.TimeoutExpired(cmd="sleep 10", timeout=5)
        exc.stdout = "already a string"
        result = decode_timeout_output(exc)
        assert result == "already a string"

    def test_decodes_with_bad_encoding(self):
        exc = subprocess.TimeoutExpired(cmd="cat broken.bin", timeout=5)
        exc.stdout = b"valid start \xff\xfe bad bytes"
        result = decode_timeout_output(exc)
        assert "valid start" in result


# ---------------------------------------------------------------------------
# timeout — agent-level integration
# ---------------------------------------------------------------------------


class TestAgentTimeout:
    """Agent loop keeps running after timeout; model sees partial output."""

    def test_timeout_does_not_crash_agent(self):
        """超时后 agent 循环继续，不崩。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Running a slow command.",
                tool_calls=[
                    _make_tool_call("c1", "bash",
                                    {"command": "sleep 100", "timeout": 1}),
                ],
            ),
            _make_response(content="Timed out. Let me try differently."),
        ]
        env = MagicMock()
        timeout_exc = subprocess.TimeoutExpired(cmd="sleep 100", timeout=1)
        timeout_exc.stdout = b"Starting long operation...\n10% complete\n"
        env.execute.side_effect = timeout_exc

        agent = Agent(model, env)
        result = agent.run("run slow task", max_steps=5)

        # Agent should NOT have crashed — exit via no_tool_calls (fallback).
        assert result["exit_status"] == "no_tool_calls"

    def test_timeout_includes_partial_output(self):
        """工具消息包含部分输出 + 超时警告。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Running a slow command.",
                tool_calls=[
                    _make_tool_call("c1", "bash",
                                    {"command": "pip install torch", "timeout": 2}),
                ],
            ),
            _make_response(content="Done."),
        ]
        env = MagicMock()
        timeout_exc = subprocess.TimeoutExpired(cmd="pip install torch", timeout=2)
        timeout_exc.stdout = b"Downloading torch-2.0.0...\n  45% 100MB/220MB\n"
        env.execute.side_effect = timeout_exc

        agent = Agent(model, env)
        result = agent.run("install torch", max_steps=5)

        tool_msg = [m for m in result["messages"] if m["role"] == "tool"][0]
        content = tool_msg["content"]
        assert "Downloading torch" in content
        assert "STILL RUNNING" in content
        assert "timeout=4" in content  # hint suggests timeout * 2
        assert "kill" in content.lower()  # warns about zombie processes

    def test_model_retries_with_higher_timeout_after_timeout(self):
        """模型看到超时后可以用更大的 timeout 重试。"""
        model = MagicMock()
        model.query.side_effect = [
            # First attempt — timeout=2.
            _make_response(
                content="Let me install the package.",
                tool_calls=[
                    _make_tool_call("c1", "bash",
                                    {"command": "pip install torch", "timeout": 2}),
                ],
            ),
            # Second attempt — model retries with timeout=120.
            _make_response(
                content="It timed out. Let me retry with a longer timeout.",
                tool_calls=[
                    _make_tool_call("c2", "bash",
                                    {"command": "pip install torch", "timeout": 120}),
                ],
            ),
            _make_response(content="Done."),
        ]
        env = MagicMock()
        timeout_exc = subprocess.TimeoutExpired(cmd="pip install torch", timeout=2)
        timeout_exc.stdout = b"Downloading... 10%\n"
        env.execute.side_effect = [
            timeout_exc,
            "Successfully installed torch-2.0.0",
        ]

        agent = Agent(model, env)
        result = agent.run("install torch", max_steps=5)

        assert result["exit_status"] == "no_tool_calls"
        assert env.execute.call_count == 2
        # Second call should use the longer timeout.
        assert env.execute.call_args_list[1] == (
            ("pip install torch",),
            {"timeout": 120},
        )

    def test_model_switches_approach_after_repeated_timeout(self):
        """多次超时后模型可以换方案而不是死循环。"""
        model = MagicMock()
        model.query.side_effect = [
            # First — pip install times out.
            _make_response(
                content="Installing dependencies.",
                tool_calls=[
                    _make_tool_call("c1", "bash",
                                    {"command": "pip install torch", "timeout": 1}),
                ],
            ),
            # Second — still times out, model pivots.
            _make_response(
                content="Still timing out. Let me check network and try "
                         "a different approach.",
                tool_calls=[
                    _make_tool_call("c2", "bash",
                                    {"command": "pip install --no-deps torch"}),
                ],
            ),
            _make_response(content="Done."),
        ]
        env = MagicMock()
        timeout_exc = subprocess.TimeoutExpired(cmd="pip install torch", timeout=1)
        timeout_exc.stdout = b"Downloading...\n"
        env.execute.side_effect = [
            timeout_exc,
            "Successfully installed torch",
        ]

        agent = Agent(model, env)
        result = agent.run("install torch", max_steps=5)

        assert result["exit_status"] == "no_tool_calls"
        # Model switched from pip install to pip install --no-deps.
        second_call = env.execute.call_args_list[1][0][0]
        assert "--no-deps" in second_call


# ---------------------------------------------------------------------------
# max_time — wall-clock budget
# ---------------------------------------------------------------------------


class TestMaxTime:
    """Agent respects the total wall-clock time budget."""

    def test_max_time_exceeded_returns_early(self):
        """max_time=0 应该立即退出。"""
        env = MagicMock()
        env.execute.return_value = ""

        agent = Agent(MagicMock(), env)
        result = agent.run("some task", max_steps=100, max_time=0)

        assert result["exit_status"] == "max_time"
        assert result["submission"] == ""

    def test_max_time_none_disables_limit(self):
        """max_time=None 时不检查时间。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Done.",
                tool_calls=[
                    _make_tool_call("c1", "submit", {"output": "all good"}),
                ],
            ),
        ]
        env = MagicMock()
        env.execute.return_value = ""

        agent = Agent(model, env)
        result = agent.run("task", max_steps=5, max_time=None)

        assert result["exit_status"] == "submitted"

    def test_max_time_not_exceeded_within_budget(self):
        """时间预算充足时正常完成。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Done.",
                tool_calls=[
                    _make_tool_call("c1", "submit", {"output": "done"}),
                ],
            ),
        ]
        env = MagicMock()
        env.execute.return_value = ""

        agent = Agent(model, env)
        result = agent.run("task", max_steps=5, max_time=3600)

        assert result["exit_status"] == "submitted"


# ---------------------------------------------------------------------------
# trajectory saving — serialize / save
# ---------------------------------------------------------------------------


def _submitting_model():
    """A model that answers with a single submit tool call."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Done.",
            tool_calls=[
                _make_tool_call("s1", "submit", {"output": "final answer"}),
            ],
        ),
    ]
    return model


class TestSerialize:
    """Tests for Agent.serialize()."""

    def test_serialize_returns_structured_dict(self):
        """serialize() 返回带 info/messages/trajectory_format 的 dict。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        data = agent.serialize()

        assert data["trajectory_format"] == "mini-agent-0.1"
        assert data["info"]["exit_status"] == "submitted"
        assert data["info"]["submission"] == "final answer"
        assert data["info"]["model_stats"]["api_calls"] == 1
        assert data["info"]["config"]["agent_type"].endswith("Agent")
        # messages must be the same list the agent used during the run
        assert data["messages"] == agent.messages
        assert data["messages"][0]["role"] == "system"

    def test_serialize_before_run_is_empty(self):
        """未运行前 serialize() 返回空轨迹。"""
        agent = Agent(MagicMock(), MagicMock())

        data = agent.serialize()

        assert data["messages"] == []
        assert data["info"]["exit_status"] == ""
        assert data["info"]["submission"] == ""
        assert data["info"]["model_stats"]["api_calls"] == 0

    def test_serialize_is_json_serializable(self):
        """serialize() 的输出可直接 json.dumps。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        json.dumps(agent.serialize())


class TestSave:
    """Tests for Agent.save()."""

    def test_save_writes_traj_json(self, tmp_path):
        """save() 写出合法的 .traj.json 文件，并返回相同数据。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        path = tmp_path / "out" / "run.traj.json"
        data = agent.save(path)

        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded == data
        assert loaded["info"]["exit_status"] == "submitted"

    def test_save_none_returns_data_without_writing(self, tmp_path):
        """save(None) 不写文件，只返回序列化数据。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        data = agent.save(None)

        assert data["info"]["exit_status"] == "submitted"
        assert list(tmp_path.iterdir()) == []

    def test_save_creates_parent_directories(self, tmp_path):
        """save() 自动创建父目录。"""
        agent = Agent(_submitting_model(), MagicMock())
        agent.run("do it")

        path = tmp_path / "a" / "b" / "c.traj.json"
        agent.save(path)

        assert path.exists()


class TestRunAutoSave:
    """Tests that run(..., output=...) saves the trajectory automatically."""

    def test_run_saves_when_output_given(self, tmp_path):
        """run(output=...) 结束后自动写出轨迹文件。"""
        agent = Agent(_submitting_model(), MagicMock())

        path = tmp_path / "run.traj.json"
        result = agent.run("do it", output=path)

        assert path.exists()
        loaded = json.loads(path.read_text())
        assert loaded["info"]["exit_status"] == result["exit_status"]
        assert loaded["info"]["submission"] == result["submission"]
        assert loaded["messages"] == result["messages"]

    def test_run_saves_on_max_steps(self, tmp_path):
        """达到 max_steps 退出时也应保存轨迹。"""
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Running.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
        )
        env = MagicMock()
        env.execute.return_value = "ok"

        agent = Agent(model, env)
        path = tmp_path / "partial.traj.json"
        result = agent.run("infinite", max_steps=3, output=path)

        assert result["exit_status"] == "max_steps"
        assert path.exists()
        assert json.loads(path.read_text())["info"]["exit_status"] == "max_steps"

    def test_run_without_output_does_not_save(self, tmp_path):
        """run(output=None) 不写文件，但 self 状态已同步可 serialize。"""
        agent = Agent(_submitting_model(), MagicMock())
        result = agent.run("do it")

        assert list(tmp_path.iterdir()) == []
        assert agent.serialize()["info"]["exit_status"] == result["exit_status"]


# ---------------------------------------------------------------------------
# cost tracking — compute_cost accumulation + cost_limit
# ---------------------------------------------------------------------------


class TestCostTracking:
    """Agent accumulates response cost and honors cost_limit."""

    def test_run_accumulates_cost(self):
        """run() 应把每次 response 的成本累加到 self.cost 并序列化。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Running.",
                tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
                usage=_make_usage(1_000_000, 1_000_000),
            ),
            _make_response(
                content="Done.",
                tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
                usage=_make_usage(1_000_000, 0),
            ),
        ]
        env = MagicMock()
        env.execute.return_value = "out"

        agent = Agent(model, env)
        result = agent.run("task")

        assert result["exit_status"] == "submitted"
        assert agent.cost > 0
        stats = agent.serialize()["info"]["model_stats"]
        assert stats["instance_cost"] == agent.cost

    def test_cost_limit_stops_agent(self):
        """累计成本超过 cost_limit 时以 exit_status='cost_limit' 退出。"""
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Running.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
            usage=_make_usage(1_000_000, 1_000_000),  # ~0.42 USD
        )
        env = MagicMock()
        env.execute.return_value = "ok"

        agent = Agent(model, env)
        result = agent.run("task", max_steps=10, cost_limit=0.01)

        assert result["exit_status"] == "cost_limit"
        # 第一次查询就超过上限，第二次循环前即停止，不再发起新调用。
        assert model.query.call_count == 1
        assert agent.cost > 0.01

    def test_cost_limit_zero_disables(self):
        """cost_limit=0 表示不限制。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Done.",
                tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
                usage=_make_usage(1_000_000, 1_000_000),
            ),
        ]
        agent = Agent(model, MagicMock())
        result = agent.run("task", cost_limit=0)
        assert result["exit_status"] == "submitted"

    def test_cost_limit_none_disables(self):
        """cost_limit=None 表示不限制。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Done.",
                tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
                usage=_make_usage(1_000_000, 1_000_000),
            ),
        ]
        agent = Agent(model, MagicMock())
        result = agent.run("task", cost_limit=None)
        assert result["exit_status"] == "submitted"


# ---------------------------------------------------------------------------
# context compression — auto-summarize history near the token limit
# ---------------------------------------------------------------------------


def _compression_query(calls):
    """A query() that does two bash round-trips then exits, summarizing between them."""
    def fake_query(messages, tools=None):
        if tools:  # 主循环查询（带工具）
            calls["loop"] += 1
            if calls["loop"] <= 2:
                return _make_response(
                    "Run.",
                    tool_calls=[
                        _make_tool_call(f"c{calls['loop']}", "bash", {"command": "ls"})
                    ],
                )
            return _make_response("Done.")  # no_tool_calls → 退出
        calls["summary"] += 1               # 摘要查询（无工具）
        return _make_response("## Task\ncompressed summary")
    return fake_query


def test_compression_triggers_and_injects_summary():
    """历史逼近上限时压缩，消息里注入带 marker 的摘要。"""
    calls = {"loop": 0, "summary": 0}
    model = MagicMock()
    model.query.side_effect = _compression_query(calls)
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    result = agent.run("task")

    assert result["exit_status"] == "no_tool_calls"
    assert calls["summary"] == 1
    assert any(
        isinstance(m.get("content"), str) and m["content"].startswith(SUMMARY_MARKER)
        for m in result["messages"]
    )


def test_compression_skips_on_summarizer_failure():
    """摘要失败时静默跳过，不注入 Error 消息。"""
    def fake_query(messages, tools=None):
        if tools:
            calls["loop"] += 1
            if calls["loop"] <= 2:
                return _make_response(
                    "Run.",
                    tool_calls=[
                        _make_tool_call(f"c{calls['loop']}", "bash", {"command": "ls"})
                    ],
                )
            return _make_response("Done.")
        raise RuntimeError("summarizer down")

    calls = {"loop": 0}
    model = MagicMock()
    model.query.side_effect = fake_query
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    result = agent.run("task")

    assert result["exit_status"] == "no_tool_calls"
    assert not any(
        m.get("role") == "user" and "Error" in str(m.get("content"))
        for m in result["messages"]
    )


def test_compression_messages_stay_aliased():
    """压缩就地更新，result/self.messages 仍指向同一 list。"""
    calls = {"loop": 0, "summary": 0}
    model = MagicMock()
    model.query.side_effect = _compression_query(calls)
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    result = agent.run("task")

    assert result["messages"] is agent.messages


def test_compression_distinguishes_summary_vs_loop_calls():
    """摘要调用（无 tools）与主循环调用（带 tools）分开统计。"""
    calls = {"loop": 0, "summary": 0}
    model = MagicMock()
    model.query.side_effect = _compression_query(calls)
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    agent.run("task")

    summary_calls = [c for c in model.query.call_args_list if "tools" not in c.kwargs]
    loop_calls = [c for c in model.query.call_args_list if "tools" in c.kwargs]
    assert len(summary_calls) == 1
    assert len(loop_calls) == 3


def test_compression_accounts_for_summary_call():
    """摘要也是一次真实 API 调用：计入 n_calls 与 cost，统计精确。"""
    calls = {"loop": 0, "summary": 0}

    def fake_query(messages, tools=None):
        if tools:  # 主循环调用零成本，便于隔离出摘要的成本
            calls["loop"] += 1
            if calls["loop"] <= 2:
                return _make_response(
                    "Run.",
                    tool_calls=[
                        _make_tool_call(f"c{calls['loop']}", "bash", {"command": "ls"})
                    ],
                    usage=_make_usage(0, 0),
                )
            return _make_response("Done.", usage=_make_usage(0, 0))
        calls["summary"] += 1  # 摘要调用：带非零 usage
        return _make_response("## Task\nsummary", usage=_make_usage(1_000_000, 0))

    model = MagicMock()
    model.query.side_effect = fake_query
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    agent.run("task")

    assert calls["summary"] == 1
    # 3 次主循环查询 + 1 次摘要查询，都应计入 api_calls
    assert agent.n_calls == calls["loop"] + calls["summary"] == 4
    assert agent.serialize()["info"]["model_stats"]["api_calls"] == 4
    # 主循环调用 usage 全为 0 → cost 完全来自摘要那次调用
    assert agent.cost > 0


def test_compression_does_not_break_no_tool_calls():
    """首轮即 no_tool_calls 时（无中间内容）不触发摘要。"""
    model = MagicMock()
    model.query.side_effect = [_make_response("Done.")]
    env = MagicMock()

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    result = agent.run("task")

    assert result["exit_status"] == "no_tool_calls"
    assert model.query.call_count == 1  # 无摘要调用
    assert [m["role"] for m in result["messages"]] == ["system", "user", "assistant"]

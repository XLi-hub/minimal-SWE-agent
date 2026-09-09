import subprocess
from unittest.mock import MagicMock

from mini_agent.agent import Agent

from ._helpers import (
    DEFAULTS,
    PRICED_DEFAULTS,
    _make_response,
    _make_tool_call,
    _make_usage,
)


# --- Agent truncation integration ---

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

    def test_query_time_is_rechecked_before_tool_dispatch(self, monkeypatch):
        """A response arriving after the deadline must not run its tools."""
        clock = iter([0.0, 0.5, 2.0])
        monkeypatch.setattr("mini_agent.agent.time.monotonic", lambda: next(clock))

        model = MagicMock()
        model.query.return_value = _make_response(
            content="Run it.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "mutate"})],
        )
        env = MagicMock()

        result = Agent(model, env).run("task", max_steps=2, max_time=1)

        assert result["exit_status"] == "max_time"
        env.execute.assert_not_called()
        tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
        assert tool_messages[0]["tool_call_id"] == "c1"
        assert "max_time" in tool_messages[0]["content"]

    def test_timeout_aware_model_receives_remaining_run_time(self):
        class TimeoutAwareModel:
            supports_request_timeout = True

            def __init__(self):
                self.timeout = None

            def query(self, messages, tools=None, timeout=None):
                self.timeout = timeout
                return _make_response(
                    content="Done.",
                    tool_calls=[
                        _make_tool_call("s1", "submit", {"output": "done"}),
                    ],
                )

        model = TimeoutAwareModel()

        result = Agent(model, MagicMock()).run("task", max_time=5)

        assert result["exit_status"] == "submitted"
        assert model.timeout is not None
        assert 0 < model.timeout <= 5

    def test_tool_timeout_is_capped_and_later_batch_calls_are_skipped(
        self, monkeypatch
    ):
        now = [0.0]
        monkeypatch.setattr("mini_agent.agent.time.monotonic", lambda: now[0])
        monkeypatch.setattr("mini_agent.tools.time.monotonic", lambda: now[0])
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Run both.",
            tool_calls=[
                _make_tool_call(
                    "c1", "bash", {"command": "first", "timeout": 99}
                ),
                _make_tool_call("c2", "bash", {"command": "second"}),
            ],
        )
        env = MagicMock()

        def execute(command, timeout=None):
            now[0] = 2.0
            return f"ran {command}"

        env.execute.side_effect = execute

        result = Agent(model, env).run("task", max_steps=2, max_time=1)

        assert result["exit_status"] == "max_time"
        env.execute.assert_called_once_with("first", timeout=1.0)
        tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
        assert [message["tool_call_id"] for message in tool_messages] == ["c1", "c2"]
        assert "max_time" in tool_messages[1]["content"]
        assert "no tool action was executed" in tool_messages[1]["content"]

    def test_edit_does_not_write_after_read_consumes_deadline(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr("mini_agent.agent.time.monotonic", lambda: now[0])
        monkeypatch.setattr("mini_agent.tools.time.monotonic", lambda: now[0])
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Edit it.",
            tool_calls=[
                _make_tool_call(
                    "e1",
                    "edit",
                    {"path": "file.txt", "old_string": "old", "new_string": "new"},
                )
            ],
        )

        class SlowReadEnvironment:
            def __init__(self):
                self.written = False

            def read_file(self, path, timeout=None):
                assert timeout == 1.0
                now[0] = 2.0
                return "old"

            def write_file(self, path, content, timeout=None):
                self.written = True

        env = SlowReadEnvironment()

        result = Agent(model, env).run("task", max_steps=2, max_time=1)

        assert result["exit_status"] == "max_time"
        assert env.written is False
        tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
        assert len(tool_messages) == 1
        assert "stopped" in tool_messages[0]["content"].lower()
        assert "max_time" in tool_messages[0]["content"]

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

        agent = Agent(model, env, config=PRICED_DEFAULTS)
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

        agent = Agent(model, env, config=PRICED_DEFAULTS)
        result = agent.run("task", max_steps=10, cost_limit=0.01)

        assert result["exit_status"] == "cost_limit"
        # 第一次查询就超过上限，工具派发前立即停止，也不再发起新调用。
        assert model.query.call_count == 1
        assert agent.cost > 0.01

    def test_query_cost_is_rechecked_before_tool_dispatch(self):
        """A costly response cannot trigger a side-effecting tool."""
        model = MagicMock()
        model.query.return_value = _make_response(
            content="Run it.",
            tool_calls=[_make_tool_call("c1", "bash", {"command": "mutate"})],
            usage=_make_usage(1_000_000, 1_000_000),
        )
        env = MagicMock()

        result = Agent(model, env, config=PRICED_DEFAULTS).run(
            "task", max_steps=2, cost_limit=0.01
        )

        assert result["exit_status"] == "cost_limit"
        env.execute.assert_not_called()
        tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
        assert tool_messages[0]["tool_call_id"] == "c1"
        assert "cost_limit" in tool_messages[0]["content"]

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

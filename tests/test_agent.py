import json
import subprocess
from unittest.mock import MagicMock

import pytest

from mini_agent.agent import Agent
from mini_agent.config import CostConfig, build_config, get_default_config
from mini_agent.tools import (
    decode_timeout_output,
    format_assistant_message,
    truncate_output,
)

DEFAULTS = get_default_config()
PRICED_DEFAULTS = DEFAULTS.model_copy(
    update={
        "cost": CostConfig(
            price_input_per_1m=0.14,
            price_input_cache_hit_per_1m=0.0028,
            price_output_per_1m=0.28,
        )
    }
)
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


def test_submission_review_requires_a_second_submit():
    """An enabled review gate turns the first valid submission into a draft."""
    config = build_config(['agent.submission_review_prompt="Audit the evidence."'])
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Initial candidate.",
            tool_calls=[_make_tool_call("draft", "submit", {"output": "draft patch"})],
        ),
        _make_response(
            content="Reviewed candidate.",
            tool_calls=[_make_tool_call("final", "submit", {"output": "final patch"})],
        ),
    ]

    result = Agent(model, MagicMock(), config=config).run("fix a bug")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "final patch"
    assert model.query.call_count == 2
    review_messages = [
        message["content"]
        for message in result["messages"]
        if message["role"] == "user" and "captured as a draft" in message["content"]
    ]
    assert review_messages == [
        "The previous submit call was captured as a draft and has not ended the run. "
        "Complete this review before submitting again:\n\nAudit the evidence."
    ]
    tool_messages = [
        message["content"] for message in result["messages"] if message["role"] == "tool"
    ]
    assert tool_messages == [
        "Draft submission captured; required review is still pending.",
        "Submitted.",
    ]


def test_submission_review_can_reset_authoring_context():
    """A clean review sees the task and draft without the author's rationale."""
    config = build_config([
        'agent.submission_review_prompt="Find an independent oracle."',
        "agent.submission_review_reset_context=true",
    ])
    seen_contexts: list[list[dict]] = []
    responses = iter([
        _make_response(
            content="Rationale that should not anchor review.",
            tool_calls=[_make_tool_call("draft", "submit", {"output": "draft diff"})],
        ),
        _make_response(
            content="Fresh review complete.",
            tool_calls=[_make_tool_call("final", "submit", {"output": "revised diff"})],
        ),
    ])
    model = MagicMock()

    def query(messages, tools=None):
        seen_contexts.append([dict(message) for message in messages])
        return next(responses)

    model.query.side_effect = query
    agent = Agent(model, MagicMock(), config=config)

    result = agent.run("fix a bug")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "revised diff"
    assert len(seen_contexts) == 2
    review_context = seen_contexts[1]
    assert [message["role"] for message in review_context] == ["system", "user", "user"]
    assert "fix a bug" in review_context[1]["content"]
    assert "draft diff" in review_context[2]["content"]
    assert "Find an independent oracle" in review_context[2]["content"]
    assert all(
        "Rationale that should not anchor review" not in str(message)
        for message in review_context
    )
    assert any(
        event["type"] == "submission_review_context_reset"
        for event in agent.events
    )
    assert any(
        event["type"] == "message"
        and event["message"].get("role") == "tool"
        and "Draft submission captured" in event["message"].get("content", "")
        for event in agent.events
    )


def test_clean_submission_review_can_search_append_only_trajectory():
    config = build_config(
        [
            'agent.submission_review_prompt="Find an independent oracle."',
            "agent.submission_review_reset_context=true",
        ],
        cli_overrides={
            "tools": {"enabled": ["bash", "submit", "trajectory"]},
        },
    )
    seen_contexts: list[list[dict]] = []
    responses = iter([
        _make_response(
            content="Author evidence: g++ emitted li4_udl.",
            tool_calls=[_make_tool_call("draft", "submit", {"output": "draft diff"})],
        ),
        _make_response(
            content="Search the author evidence without trusting its conclusion.",
            tool_calls=[
                _make_tool_call(
                    "history",
                    "trajectory",
                    {"query": "g++ emitted"},
                )
            ],
        ),
        _make_response(
            content="Review complete.",
            tool_calls=[_make_tool_call("final", "submit", {"output": "revised diff"})],
        ),
    ])
    model = MagicMock()

    def query(messages, tools=None):
        seen_contexts.append([dict(message) for message in messages])
        return next(responses)

    model.query.side_effect = query

    result = Agent(model, MagicMock(), config=config).run("fix a bug")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "revised diff"
    assert "Author evidence" not in str(seen_contexts[1])
    trajectory_results = [
        message["content"]
        for message in seen_contexts[2]
        if message.get("role") == "tool"
    ]
    assert len(trajectory_results) == 1
    assert "g++ emitted li4_udl" in trajectory_results[0]


def test_clean_review_receives_evidence_checkpoint_and_untrusted_summary():
    config = build_config([
        'agent.submission_review_prompt="Find an independent oracle."',
        "agent.submission_review_reset_context=true",
        "agent.submission_review_checkpoint_context=true",
    ])
    responses = iter([
        _make_response(
            content="Author theory that must not survive verbatim.",
            tool_calls=[
                _make_tool_call("check", "bash", {"command": "pytest tests/test_fix.py"})
            ],
        ),
        _make_response(
            content="Draft based on the author theory.",
            tool_calls=[_make_tool_call("draft", "submit", {"output": "large draft diff"})],
        ),
        _make_response(content="Structured but lossy author progress summary."),
        _make_response(
            content="Independent review complete.",
            tool_calls=[_make_tool_call("final", "submit", {"output": "reviewed diff"})],
        ),
    ])
    seen_calls: list[tuple[list[dict], object]] = []
    model = MagicMock()

    def query(messages, tools=None):
        seen_calls.append(([dict(message) for message in messages], tools))
        return next(responses)

    model.query.side_effect = query
    env = MagicMock()
    env.execute.return_value = {
        "output": "1 passed",
        "returncode": 0,
        "exception_info": "",
    }

    agent = Agent(model, env, config=config)
    result = agent.run("fix a bug")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "reviewed diff"
    assert agent._steps == 3
    assert agent.n_calls == 4
    assert len(seen_calls) == 4

    summary_prompt = seen_calls[2][0][0]["content"]
    assert seen_calls[2][1] is None
    assert "pytest tests/test_fix.py" in summary_prompt
    assert "1 passed" in summary_prompt
    assert "large draft diff" not in summary_prompt

    review_context = seen_calls[3][0]
    assert [message["role"] for message in review_context] == ["system", "user", "user"]
    review_prompt = review_context[-1]["content"]
    assert "<author_evidence_checkpoint>" in review_prompt
    assert "command: `pytest tests/test_fix.py`" in review_prompt
    assert "returncode=0" in review_prompt
    assert "<untrusted_author_working_memory>" in review_prompt
    assert "Structured but lossy author progress summary." in review_prompt
    assert "Author theory that must not survive verbatim." not in review_prompt
    assert "<candidate_patch>\nlarge draft diff" in review_prompt
    assert any(
        event["type"] == "submission_review_checkpoint"
        and event["summary_status"] == "generated"
        for event in agent.events
    )


def test_clean_review_falls_back_to_machine_checkpoint_when_summary_fails():
    config = build_config([
        'agent.submission_review_prompt="Audit independently."',
        "agent.submission_review_reset_context=true",
        "agent.submission_review_checkpoint_context=true",
    ])
    agent_responses = iter([
        _make_response(
            content="Collect evidence.",
            tool_calls=[_make_tool_call("check", "bash", {"command": "pytest -q"})],
        ),
        _make_response(
            content="Submit draft.",
            tool_calls=[_make_tool_call("draft", "submit", {"output": "draft"})],
        ),
        _make_response(
            content="Review done.",
            tool_calls=[_make_tool_call("final", "submit", {"output": "final"})],
        ),
    ])
    review_contexts: list[list[dict]] = []
    model = MagicMock()

    def query(messages, tools=None):
        if tools is None:
            raise RuntimeError("summary provider unavailable")
        review_contexts.append([dict(message) for message in messages])
        return next(agent_responses)

    model.query.side_effect = query
    env = MagicMock()
    env.execute.return_value = "test output"
    agent = Agent(model, env, config=config)

    result = agent.run("fix a bug")

    assert result["exit_status"] == "submitted"
    review_prompt = review_contexts[-1][-1]["content"]
    assert "<author_evidence_checkpoint>" in review_prompt
    assert "command: `pytest -q`" in review_prompt
    assert "<untrusted_author_working_memory>" not in review_prompt
    assert any(
        event["type"] == "submission_review_checkpoint"
        and event["summary_status"] == "error"
        and "summary provider unavailable" in event["summary_error"]
        for event in agent.events
    )


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


@pytest.mark.parametrize("submit_index", [0, 1, 2])
def test_submit_batch_acknowledges_every_tool_call_and_skips_remaining(submit_index):
    """Every call in a submit batch gets a response, in any position."""
    submit = _make_tool_call("submit", "submit", {"output": "done"})
    before = _make_tool_call("before", "bash", {"command": "before"})
    unknown = _make_tool_call("unknown", "not_registered", {})
    malformed = _make_tool_call("malformed", "bash", {"command": "after"})
    malformed.function.arguments = "{not-json"
    after = _make_tool_call("after", "bash", {"command": "after"})

    batches = [
        [submit, after, unknown, malformed],
        [before, submit, unknown, malformed],
        [unknown, malformed, submit],
    ]
    tool_calls = batches[submit_index]

    model = MagicMock()
    model.query.return_value = _make_response(
        content="Done.",
        tool_calls=tool_calls,
    )
    env = MagicMock()
    env.execute.return_value = "ok"

    result = Agent(model, env).run("submit")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "done"
    assert model.query.call_count == 1
    tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == [tc.id for tc in tool_calls]

    # Only a valid bash call before submit is allowed to execute.
    expected_commands = ["before"] if submit_index == 1 else []
    assert [call.args[0] for call in env.execute.call_args_list] == expected_commands
    for index, (message, tc) in enumerate(zip(tool_messages, tool_calls)):
        if index > submit_index:
            assert message["content"].startswith("Skipped tool '")


def test_invalid_submit_arguments_get_a_tool_error_and_do_not_exit():
    """A malformed/missing submit payload must not bypass later calls."""
    model = MagicMock()
    model.query.side_effect = [
        _make_response(
            content="Try.",
            tool_calls=[
                _make_tool_call("bad-submit", "submit", {}),
                _make_tool_call("bash", "bash", {"command": "echo ok"}),
            ],
        ),
        _make_response(content="No more work."),
    ]
    env = MagicMock()
    env.execute.return_value = "ok"

    result = Agent(model, env).run("test invalid submit")

    assert result["exit_status"] == "no_tool_calls"
    assert env.execute.call_args_list[0].args[0] == "echo ok"
    tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["bad-submit", "bash"]
    assert "requires an 'output'" in tool_messages[0]["content"]


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

    @pytest.mark.parametrize("max_chars", range(0, 9))
    def test_character_budget_is_never_exceeded_for_tiny_limits(self, max_chars):
        result = truncate_output("头" * 100 + "尾", max_lines=100, max_chars=max_chars)
        assert len(result) <= max_chars


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


def test_model_query_exception_exits_with_diagnostics():
    """Uncaught model/config errors stop immediately and preserve diagnostics."""
    model = MagicMock()
    model.query.side_effect = RuntimeError("invalid API credentials")
    env = MagicMock()

    agent = Agent(model, env)
    result = agent.run("test")

    # 错误被追加为 user 消息
    user_msgs = [m for m in result["messages"] if m["role"] == "user"]
    error_msg = [m for m in user_msgs if "invalid API credentials" in str(m["content"])]
    assert len(error_msg) == 1
    assert result["exit_status"] == "error"
    assert result["error"]["type"] == "RuntimeError"
    assert "invalid API credentials" in result["error"]["message"]
    assert model.query.call_count == 1


def test_no_tool_call_can_be_retried_by_config():
    config = DEFAULTS.model_copy(
        update={
            "agent": DEFAULTS.agent.model_copy(update={"no_tool_call_retries": 1})
        }
    )
    model = MagicMock()
    model.query.side_effect = [
        _make_response(content="I forgot the tool."),
        _make_response(
            content="Done.",
            tool_calls=[_make_tool_call("s1", "submit", {"output": "ok"})],
        ),
    ]

    result = Agent(model, MagicMock(), config=config).run("test")

    assert result["exit_status"] == "submitted"
    assert result["submission"] == "ok"
    assert any(
        "did not call a tool" in str(message["content"])
        for message in result["messages"]
        if message["role"] == "user"
    )


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

        assert data["trajectory_format"] == "mini-agent-0.2"
        assert data["info"]["exit_status"] == "submitted"
        assert data["info"]["submission"] == "final answer"
        assert data["info"]["model_stats"]["api_calls"] == 1
        assert data["info"]["config"]["agent_type"].endswith("Agent")
        assert data["info"]["config"]["model"]["model_name"] == "gpt-4o-mini"
        assert data["info"]["config"]["environment"]["type"] == "local"
        assert data["info"]["mini_version"] == "0.1.0"
        # messages must be the same list the agent used during the run
        assert data["messages"] == agent.messages
        assert data["messages"][0]["role"] == "system"
        assert [event["sequence"] for event in data["events"]] == list(
            range(len(data["events"]))
        )
        assert [event["type"] for event in data["events"]] == [
            "message", "message", "message", "message"
        ]

    def test_serialize_before_run_is_empty(self):
        """未运行前 serialize() 返回空轨迹。"""
        agent = Agent(MagicMock(), MagicMock())

        data = agent.serialize()

        assert data["messages"] == []
        assert data["events"] == []
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
        assert "events" not in loaded
        event_path = path.with_name("run.events.jsonl")
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        assert loaded["event_log"]["path"] == event_path.name
        assert loaded["event_log"]["event_count"] == len(events) == 4

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
        assert (path.parent / "c.events.jsonl").exists()


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
        event_path = tmp_path / "run.events.jsonl"
        assert event_path.exists()
        assert loaded["event_log"]["event_count"] == len(
            event_path.read_text().splitlines()
        )

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

    def test_event_sidecar_is_journalled_before_run_finishes(self, tmp_path):
        """工具执行期间已能看到先前事件，而不是只在 finally 一次性写入。"""
        model = MagicMock()
        model.query.side_effect = [
            _make_response(
                content="Run.",
                tool_calls=[_make_tool_call("c1", "bash", {"command": "echo ok"})],
            ),
            _make_response(
                content="Done.",
                tool_calls=[_make_tool_call("s1", "submit", {"output": "done"})],
            ),
        ]
        event_path = tmp_path / "live.events.jsonl"
        observed_sequences = []

        def execute(command, timeout=None):
            observed_sequences.extend(
                json.loads(line)["sequence"]
                for line in event_path.read_text().splitlines()
            )
            return "ok"

        env = MagicMock()
        env.execute.side_effect = execute
        result = Agent(model, env).run("task", output=tmp_path / "live.traj.json")

        assert result["exit_status"] == "submitted"
        assert observed_sequences == [0, 1, 2]

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


def test_compression_preserves_raw_messages_in_append_only_events():
    """压缩只改变模型 context，原始 assistant/tool 消息仍完整留在事件流。"""
    calls = {"loop": 0, "summary": 0}
    model = MagicMock()
    model.query.side_effect = _compression_query(calls)
    env = MagicMock()
    env.execute.return_value = "out"

    agent = Agent(model, env, context_window=100, reserve_tokens=10, keep_last_n_turns=1)
    result = agent.run("task")
    data = agent.serialize()

    raw_messages = [
        event["message"] for event in data["events"] if event["type"] == "message"
    ]
    raw_tool_ids = [
        message["tool_call_id"] for message in raw_messages if message["role"] == "tool"
    ]
    compression_events = [
        event for event in data["events"] if event["type"] == "context_compression"
    ]

    assert raw_tool_ids == ["c1", "c2"]
    assert len(raw_messages) > len(result["messages"])
    assert len(compression_events) == 1
    assert compression_events[0]["summary_message"]["content"].startswith(SUMMARY_MARKER)
    assert compression_events[0]["context_messages"] == result["messages"][:-1]
    # Only the compressed context view is sent to later loop queries; the
    # append-only event list is an audit artifact, not an extra model input.
    assert all(call.args[0] is not data["events"] for call in model.query.call_args_list)


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

    agent = Agent(
        model,
        env,
        config=PRICED_DEFAULTS,
        context_window=100,
        reserve_tokens=10,
        keep_last_n_turns=1,
    )
    agent.run("task")

    assert calls["summary"] == 1
    # 3 次主循环查询 + 1 次摘要查询，都应计入 api_calls
    assert agent.n_calls == calls["loop"] + calls["summary"] == 4
    assert agent.serialize()["info"]["model_stats"]["api_calls"] == 4
    # 主循环调用 usage 全为 0 → cost 完全来自摘要那次调用
    assert agent.cost > 0


def test_summary_cost_limit_stops_before_next_loop_query():
    """A summary request that exhausts the budget prevents another API call."""
    calls = {"loop": 0, "summary": 0}

    def fake_query(messages, tools=None):
        if tools:
            calls["loop"] += 1
            return _make_response(
                "Run.",
                tool_calls=[
                    _make_tool_call(f"c{calls['loop']}", "bash", {"command": "ls"})
                ],
                usage=_make_usage(0, 0),
            )
        calls["summary"] += 1
        return _make_response("## Task\nsummary", usage=_make_usage(1_000_000, 0))

    model = MagicMock()
    model.query.side_effect = fake_query
    env = MagicMock()
    env.execute.return_value = "out"

    result = Agent(
        model,
        env,
        config=PRICED_DEFAULTS,
        context_window=100,
        reserve_tokens=10,
        keep_last_n_turns=0,
    ).run("task", cost_limit=0.01)

    assert result["exit_status"] == "cost_limit"
    assert calls == {"loop": 1, "summary": 1}
    assert model.query.call_count == 2


def test_summary_time_limit_stops_before_next_loop_query(monkeypatch):
    """A successful summary that exhausts wall time prevents another API call."""
    clock = [0.0]
    calls = {"loop": 0, "summary": 0}
    monkeypatch.setattr("mini_agent.agent.time.monotonic", lambda: clock[0])

    def fake_query(messages, tools=None):
        if tools:
            calls["loop"] += 1
            return _make_response(
                "Run.",
                tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
            )
        calls["summary"] += 1
        clock[0] = 2.0
        return _make_response("## Task\nsummary")

    model = MagicMock()
    model.query.side_effect = fake_query
    env = MagicMock()
    env.execute.return_value = "out"

    result = Agent(
        model,
        env,
        context_window=100,
        reserve_tokens=10,
        keep_last_n_turns=0,
    ).run("task", max_time=1)

    assert result["exit_status"] == "max_time"
    assert calls == {"loop": 1, "summary": 1}


def test_malformed_summary_response_is_accounted_before_budget_check():
    """Billable usage is retained even when the summary body cannot be parsed."""
    calls = {"loop": 0, "summary": 0}

    def fake_query(messages, tools=None):
        if tools:
            calls["loop"] += 1
            return _make_response(
                "Run.",
                tool_calls=[_make_tool_call("c1", "bash", {"command": "ls"})],
                usage=_make_usage(0, 0),
            )
        calls["summary"] += 1
        response = _make_response("unused", usage=_make_usage(1_000_000, 0))
        response.choices = []
        return response

    model = MagicMock()
    model.query.side_effect = fake_query
    env = MagicMock()
    env.execute.return_value = "out"
    agent = Agent(
        model,
        env,
        config=PRICED_DEFAULTS,
        context_window=100,
        reserve_tokens=10,
        keep_last_n_turns=0,
    )

    result = agent.run("task", cost_limit=0.01)

    assert result["exit_status"] == "cost_limit"
    assert calls == {"loop": 1, "summary": 1}
    assert agent.n_calls == 2
    assert agent.cost > 0.01


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

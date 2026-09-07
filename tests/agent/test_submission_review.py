from unittest.mock import MagicMock

import pytest

from mini_agent.agent import Agent
from mini_agent.config import build_config

from ._helpers import _make_response, _make_tool_call


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

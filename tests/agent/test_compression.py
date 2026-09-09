from unittest.mock import MagicMock

from mini_agent.agent import Agent

from ._helpers import (
    PRICED_DEFAULTS,
    SUMMARY_MARKER,
    _make_response,
    _make_tool_call,
    _make_usage,
)


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
    """摘要失败时记录结构化事件，但不向模型注入 Error 消息。"""
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
    failure_events = [
        event
        for event in agent.events
        if event["type"] == "context_compression_failed"
    ]
    assert len(failure_events) == 1
    assert failure_events[0]["error_type"] == "RuntimeError"
    assert failure_events[0]["error_message"] == "summarizer down"
    assert failure_events[0]["messages_before"] == 6


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

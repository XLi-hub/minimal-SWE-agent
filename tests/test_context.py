"""Unit tests for context compression (token estimation + LLM summarization)."""

from unittest.mock import MagicMock

from mini_agent.config import get_default_config
from mini_agent.tools import TOOL_REGISTRY
from mini_agent.context import (
    compress,
    count_tokens,
    estimate_tokens,
    flatten,
    group_round_trips,
    should_compress,
    summarize,
)

DEFAULTS = get_default_config()
BASH_TOOL = TOOL_REGISTRY["bash"].schema
SUMMARY_MARKER = DEFAULTS.agent.summary_marker


# --- helpers ---

def _summary_model(content="## Task\nsummarized"):
    """A mock model whose query() returns a summary string (no tools)."""
    model = MagicMock()
    resp = MagicMock()
    resp.choices[0].message.content = content
    model.query.return_value = resp
    return model


def _tool_call(id_, name="bash", arguments='{"command": "ls"}'):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


# --- estimate_tokens / count_tokens ---


def test_estimate_tokens():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_count_tokens_sums_content_plus_overhead():
    assert count_tokens([{"role": "user", "content": "abcd"}]) == 4 + 1


def test_count_tokens_handles_none_content_and_tool_calls():
    # assistant 只带 tool_calls 时 content 可为 None，不应崩溃
    total = count_tokens([{"role": "assistant", "content": None,
                           "tool_calls": [_tool_call("c1")]}])
    assert total > 0


def test_count_tokens_includes_tools():
    msgs = [{"role": "user", "content": "hi"}]
    assert count_tokens(msgs, tools=[BASH_TOOL]) > count_tokens(msgs)


def test_should_compress_threshold():
    msgs = [{"role": "user", "content": "x" * 400}]  # ~100 tokens
    assert should_compress(msgs, [BASH_TOOL], context_window=100, threshold=0.8, reserve=0)
    assert not should_compress(msgs, [BASH_TOOL], context_window=10000, threshold=0.8, reserve=0)


# --- group_round_trips ---


def test_group_round_trips_keeps_assistant_and_tools_together():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a1", "tool_calls": [_tool_call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "o1"},
        {"role": "assistant", "content": "a2", "tool_calls": [_tool_call("c2")]},
        {"role": "tool", "tool_call_id": "c2", "content": "o2"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "error"},
    ]
    units = group_round_trips(messages)
    assert len(units) == 6
    assert len(units[2]) == 2  # assistant + its tool result
    assert len(units[3]) == 2
    assert units[4] == [messages[6]]  # 无 tool_calls 的 assistant 单独成单元


def test_group_round_trips_single_messages_are_own_units():
    messages = [{"role": "user", "content": "hi"}]
    assert group_round_trips(messages) == [[messages[0]]]


def test_group_round_trips_orphan_tool_is_own_unit():
    # 文档化的防御边界：孤儿 tool 消息独立成单元（不会去拆别的对）
    messages = [{"role": "tool", "tool_call_id": "x", "content": "orphan"}]
    assert group_round_trips(messages) == [[messages[0]]]


# --- flatten ---


def test_flatten_includes_tool_name_args_and_results():
    units = [[
        {"role": "assistant", "content": "run",
         "tool_calls": [_tool_call("c1", "bash", '{"command": "ls"}')]},
        {"role": "tool", "tool_call_id": "c1", "content": "file1\nfile2"},
    ]]
    out = flatten(units)
    assert "assistant" in out
    assert "bash" in out
    assert "ls" in out
    assert "file1" in out
    assert "tool result" in out


def test_flatten_handles_none_content():
    units = [[{"role": "assistant", "content": None, "tool_calls": [_tool_call("c1")]}]]
    assert "assistant" in flatten(units)  # 不应崩溃


# --- summarize ---


def test_summarize_calls_model_without_tools():
    model = _summary_model("the summary")
    text, response = summarize(model, "old", "new lines")
    assert text == "the summary"
    assert response is model.query.return_value  # 返回完整 response 供记账
    assert model.query.call_count == 1
    msgs = model.query.call_args.args[0]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "old" in msgs[0]["content"]
    assert "new lines" in msgs[0]["content"]
    assert "tools" not in model.query.call_args.kwargs


# --- compress ---


def _three_round_trips():
    """[system, task, (assistant+tool) x3] — 5 个单元。"""
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "step1", "tool_calls": [_tool_call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "out1"},
        {"role": "assistant", "content": "step2", "tool_calls": [_tool_call("c2")]},
        {"role": "tool", "tool_call_id": "c2", "content": "out2"},
        {"role": "assistant", "content": "step3", "tool_calls": [_tool_call("c3")]},
        {"role": "tool", "tool_call_id": "c3", "content": "out3"},
    ]
    return messages


def test_compress_pins_system_and_task_and_keeps_tail():
    messages = _three_round_trips()
    model = _summary_model("SUMMARY TEXT")
    result, response = compress(messages, model, keep_last_n_turns=1)

    assert response is model.query.return_value  # 发生摘要 → 返回 response 供记账
    assert result[0] is messages[0]          # system 永不动
    assert result[1] is messages[1]          # 任务永不动
    assert result[2]["role"] == "user"
    assert result[2]["content"].startswith(SUMMARY_MARKER)
    assert "SUMMARY TEXT" in result[2]["content"]
    # 尾部 = 最近 1 轮 verbatim（step3 + tool c3）
    assert result[3] is messages[6]
    assert result[4] is messages[7]
    assert len(result) == 5


def test_compress_never_orphans_tool():
    messages = _three_round_trips()
    result, _ = compress(messages, _summary_model(), keep_last_n_turns=1)
    # 结果里每条 tool 消息都必须在「assistant(tool_calls) + tools」单元里
    for unit in group_round_trips(result):
        if any(m["role"] == "tool" for m in unit):
            assert unit[0]["role"] == "assistant"
            assert unit[0].get("tool_calls")


def test_compress_folds_in_existing_summary():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "user", "content": f"{SUMMARY_MARKER}\nOLD SUMMARY BODY"},
        {"role": "assistant", "content": "step1", "tool_calls": [_tool_call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "out1"},
        {"role": "assistant", "content": "step2", "tool_calls": [_tool_call("c2")]},
        {"role": "tool", "tool_call_id": "c2", "content": "out2"},
    ]
    model = _summary_model("NEW SUMMARY")
    result, response = compress(messages, model, keep_last_n_turns=1)
    assert response is model.query.return_value

    # 旧摘要内容（剥离 marker 后）被当作 existing_summary 传入 summarizer
    prompt = model.query.call_args.args[0][0]["content"]
    assert "OLD SUMMARY BODY" in prompt
    # 结果里只保留一个新 marker，不出现嵌套旧摘要
    markers = [m for m in result
               if isinstance(m.get("content"), str)
               and m["content"].startswith(SUMMARY_MARKER)]
    assert len(markers) == 1
    assert "NEW SUMMARY" in markers[0]["content"]


def test_compress_no_middle_returns_unchanged():
    messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "TASK"}]
    model = MagicMock()
    result, response = compress(messages, model)
    assert result == messages
    assert response is None  # 没有中间内容 → 无摘要响应，供调用方跳过记账
    assert model.query.call_count == 0  # 没有中间内容，不调 summarizer


def test_compress_keep_zero_yields_system_task_summary():
    messages = _three_round_trips()
    result, response = compress(messages, _summary_model("S"), keep_last_n_turns=0)
    assert response is not None
    assert [m["role"] for m in result] == ["system", "user", "user"]
    assert result[2]["content"].startswith(SUMMARY_MARKER)


def test_compress_clamps_keep_larger_than_units():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "TASK"},
        {"role": "assistant", "content": "step", "tool_calls": [_tool_call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
    ]
    result, response = compress(messages, _summary_model("S"), keep_last_n_turns=100)
    # keep 被夹到 n-2=1 → 无中间内容 → 原样返回，不重复 system/task
    assert result == messages
    assert response is None
    assert sum(1 for m in result if m["role"] == "system") == 1

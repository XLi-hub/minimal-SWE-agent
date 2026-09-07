from unittest.mock import MagicMock

from mini_agent.agent import Agent

from ._helpers import DEFAULTS, _make_response, _make_tool_call


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

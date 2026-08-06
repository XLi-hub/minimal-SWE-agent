"""Unit tests for Model — mock OpenAI client, no API key needed."""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.mini_agent.model import Model, query_lm


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_global_model():
    """每个测试前重置全局 _model，避免状态污染。"""
    import src.mini_agent.model as mod

    mod._model = None


@pytest.fixture()
def mock_openai():
    """Mock OpenAI + httpx client，注入假的 API key。

    所有测试共享同一个 mock 模式——验证我们怎么调 API，
    而不是 API 返回什么。
    """
    with patch("httpx.Client") as mock_http, \
         patch("src.mini_agent.model.OpenAI") as mock_cls, \
         patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):

        mock_http_client = MagicMock()
        mock_http.return_value = mock_http_client

        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        yield mock_client


# ---------------------------------------------------------------------------
# Model.query 单元测试
# ---------------------------------------------------------------------------

def test_query_passes_messages_to_client(mock_openai):
    """验证 messages 原样传给 OpenAI client。"""
    model = Model()
    messages = [{"role": "user", "content": "Hello"}]

    model.query(messages)

    kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert kwargs["messages"] == messages


def test_query_forwards_tools_when_provided(mock_openai):
    """验证 tools 参数透传给 API。"""
    model = Model()
    tools = [{"type": "function", "function": {"name": "bash"}}]

    model.query([{"role": "user", "content": "Run"}], tools=tools)

    kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert "tools" in kwargs
    assert kwargs["tools"] == tools


def test_query_omits_tools_when_not_provided(mock_openai):
    """不传 tools 时不应出现 tools 参数。"""
    model = Model()
    model.query([{"role": "user", "content": "Hi"}])

    kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert "tools" not in kwargs


def test_query_returns_full_response_object(mock_openai):
    """query() 返回完整的 OpenAI response，不是只返回文本。"""
    model = Model()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Hello back"
    mock_openai.chat.completions.create.return_value = mock_response

    result = model.query([{"role": "user", "content": "Hi"}])

    assert result is mock_response


def test_query_uses_deepseek_chat_model(mock_openai):
    """验证模型名始终为 deepseek-chat。"""
    model = Model()
    model.query([{"role": "user", "content": "ping"}])

    kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "deepseek-chat"


# ---------------------------------------------------------------------------
# query_lm 便捷函数测试
# ---------------------------------------------------------------------------

def test_query_lm_extracts_text_content(mock_openai):
    """query_lm 应返回 .choices[0].message.content 文本。"""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "only the text"
    mock_openai.chat.completions.create.return_value = mock_response

    result = query_lm([{"role": "user", "content": "Hi"}])

    assert isinstance(result, str)
    assert result == "only the text"


def test_query_lm_passes_multi_turn_messages(mock_openai):
    """query_lm 应透传多轮对话消息给 API。"""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Test"
    mock_openai.chat.completions.create.return_value = mock_response

    messages = [
        {"role": "user", "content": "Remember: my name is Test."},
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "What is my name?"},
    ]
    query_lm(messages)

    kwargs = mock_openai.chat.completions.create.call_args.kwargs
    assert kwargs["messages"] == messages
    assert len(kwargs["messages"]) == 3

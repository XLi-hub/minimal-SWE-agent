from src.mini_agent.model import query_lm


def test_returns_non_empty_string():
    """查询 LM 应返回非空字符串。"""
    messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    result = query_lm(messages)
    assert isinstance(result, str)
    assert len(result) > 0


def test_simple_math():
    """简单问答应返回正确结果。"""
    messages = [{"role": "user", "content": "What is 1+1? Reply in one word."}]
    result = query_lm(messages)
    assert "2" in result or "two" in result.lower()


def test_multi_turn_memory():
    """多轮对话应保持上下文。"""
    messages = [
        {"role": "user", "content": "Remember: my name is Test."},
        {"role": "assistant", "content": "Got it, your name is Test."},
        {"role": "user", "content": "What is my name? One word only."},
    ]
    result = query_lm(messages)
    assert "test" in result.lower()

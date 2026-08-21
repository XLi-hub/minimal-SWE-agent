"""Unit tests for compute_cost — mock usage objects, no API needed."""

from unittest.mock import MagicMock

from mini_agent.config import get_default_config
from mini_agent.cost import compute_cost

PRICES = get_default_config().cost


def _make_response(usage=None):
    response = MagicMock()
    response.usage = usage
    return response


def _make_usage(prompt_tokens, completion_tokens, cached_tokens=None):
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    if cached_tokens is not None:
        usage.prompt_tokens_details.cached_tokens = cached_tokens
    return usage


def test_missing_usage_returns_zero():
    """usage 为 None 时成本为 0。"""
    assert compute_cost(_make_response(None)) == 0.0


def test_no_usage_attribute_returns_zero():
    """response 没有真实 usage（如裸 MagicMock）时不崩溃，返回 0。"""
    assert compute_cost(MagicMock()) == 0.0


def test_non_numeric_tokens_return_zero():
    """token 数不是整数时返回 0。"""
    usage = MagicMock()
    usage.prompt_tokens = "not an int"
    usage.completion_tokens = 100
    assert compute_cost(_make_response(usage)) == 0.0


def test_no_cached_tokens_uses_full_input_rate():
    """无缓存命中时，输入全部按 cache-miss 价 + 输出价。"""
    usage = _make_usage(1_000_000, 1_000_000)
    expected = round(PRICES.price_input_per_1m + PRICES.price_output_per_1m, 8)
    assert compute_cost(_make_response(usage)) == expected


def test_cached_tokens_split_input_rate():
    """缓存命中部分按 cache-hit 价，未命中按 miss 价。"""
    usage = _make_usage(1_000_000, 0, cached_tokens=500_000)
    expected = round(
        500_000 / 1e6 * PRICES.price_input_per_1m
        + 500_000 / 1e6 * PRICES.price_input_cache_hit_per_1m,
        8,
    )
    assert compute_cost(_make_response(usage)) == expected


def test_cached_tokens_clamped_to_prompt():
    """cached_tokens 超过 prompt_tokens 时被 clamp 到 prompt_tokens。"""
    usage = _make_usage(100, 0, cached_tokens=1000)
    expected = round(100 / 1e6 * PRICES.price_input_cache_hit_per_1m, 8)
    assert compute_cost(_make_response(usage)) == expected


def test_cached_tokens_non_numeric_treated_as_zero():
    """cached_tokens 非整数时按 0 处理（全部 miss）。"""
    usage = _make_usage(1_000_000, 0)
    usage.prompt_tokens_details.cached_tokens = "abc"
    expected = round(PRICES.price_input_per_1m, 8)
    assert compute_cost(_make_response(usage)) == expected

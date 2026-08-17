"""Cost calculation from a model response's ``usage`` block.

DeepSeek's ``deepseek-chat`` exposes token counts on the OpenAI-compatible
response (``usage.prompt_tokens``, ``usage.completion_tokens``, and — on
cache hits — ``usage.prompt_tokens_details.cached_tokens``).  We turn those
into a USD figure using the per-1M-token prices defined in ``config``.
"""

from src.mini_agent.config import (
    PRICE_INPUT_CACHE_HIT_PER_1M,
    PRICE_INPUT_PER_1M,
    PRICE_OUTPUT_PER_1M,
)


def compute_cost(response) -> float:
    """Return the USD cost of a model response, or ``0.0`` when unknown.

    The cost is split by cache hit vs. miss on the input side: cached
    input tokens are much cheaper than fresh ones.  Any missing or
    non-numeric usage field (e.g. a mock without ``.usage``) yields
    ``0.0`` so callers can accumulate unconditionally.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0

    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return 0.0

    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    if not isinstance(cached, int):
        cached = 0
    cached = min(cached, prompt)  # cache hits can't exceed total input

    miss = prompt - cached
    cost = (
        miss / 1e6 * PRICE_INPUT_PER_1M
        + cached / 1e6 * PRICE_INPUT_CACHE_HIT_PER_1M
        + completion / 1e6 * PRICE_OUTPUT_PER_1M
    )
    return round(cost, 8)

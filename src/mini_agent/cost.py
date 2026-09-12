"""Cost calculation from a model response's ``usage`` block.

OpenAI-compatible chat completion responses expose token counts on
``usage.prompt_tokens`` and ``usage.completion_tokens``.  Cache hits may use
OpenAI's nested ``usage.prompt_tokens_details.cached_tokens`` field or
DeepSeek's top-level ``usage.prompt_cache_hit_tokens`` field.  We turn those
into a USD figure using the per-1M-token prices defined in ``config``.
"""

from mini_agent.config import Config, get_default_config


def compute_cost(response, config: Config | None = None) -> float:
    """Return the USD cost of a model response, or ``0.0`` when unknown.

    The cost is split by cache hit vs. miss on the input side: cached
    input tokens are much cheaper than fresh ones.  Any missing or
    non-numeric usage field (e.g. a mock without ``.usage``) yields
    ``0.0`` so callers can accumulate unconditionally.

    ``config`` is optional — when omitted the prices from ``default.yaml``
    are used.
    """
    prices = (config or get_default_config()).cost

    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0

    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return 0.0

    details = getattr(usage, "prompt_tokens_details", None)
    cached = (
        getattr(details, "cached_tokens", None) if details is not None else None
    )
    if not isinstance(cached, int):
        cached = getattr(usage, "prompt_cache_hit_tokens", 0)
    if not isinstance(cached, int):
        cached = 0
    cached = max(0, min(cached, prompt))  # cache hits can't exceed total input

    miss = prompt - cached
    cost = (
        miss / 1e6 * prices.price_input_per_1m
        + cached / 1e6 * prices.price_input_cache_hit_per_1m
        + completion / 1e6 * prices.price_output_per_1m
    )
    return round(cost, 8)

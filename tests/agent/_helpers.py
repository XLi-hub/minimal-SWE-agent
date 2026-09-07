import json
from unittest.mock import MagicMock

from mini_agent.config import CostConfig, get_default_config


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

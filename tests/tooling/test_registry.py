"""Tests for tool registration and enabled schema configuration."""

import pytest

from mini_agent.config import build_config
from mini_agent.tools import (
    TOOL_REGISTRY,
    get_enabled_tool_schemas,
)


def test_registry_pairs_every_name_with_matching_schema():
    for name, definition in TOOL_REGISTRY.items():
        assert definition.name == name
        assert definition.schema["function"]["name"] == name
        assert callable(definition.handler)


def test_enabled_schemas_follow_configured_order():
    cfg = build_config(["default_bash"])
    schemas = get_enabled_tool_schemas(cfg)
    assert [schema["function"]["name"] for schema in schemas] == ["bash", "submit"]


def test_unregistered_configured_tool_is_rejected():
    cfg = build_config(['tools.enabled=["bash","missing"]'])
    with pytest.raises(ValueError, match="not registered"):
        get_enabled_tool_schemas(cfg)

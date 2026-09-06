"""Shared data types for the tool registry and handlers.

The execution loop keeps these small immutable value objects at its boundary;
the concrete handlers and schemas live in their own modules.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from mini_agent.config import Config


@dataclass(frozen=True)
class ToolContext:
    """Dependencies shared by tool handlers."""

    environment: Any
    config: Config
    event_log: Sequence[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ToolResult:
    """A handler result; ``submission`` requests a successful agent exit."""

    content: str
    submission: str | None = None


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    """Everything needed to advertise and execute one tool."""

    name: str
    schema: dict[str, Any]
    handler: ToolHandler

    def __post_init__(self) -> None:
        schema_name = self.schema.get("function", {}).get("name")
        if schema_name != self.name:
            raise ValueError(
                f"tool registry name {self.name!r} does not match schema "
                f"name {schema_name!r}"
            )

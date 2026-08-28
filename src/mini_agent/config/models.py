"""Typed configuration models (pydantic v2).

These mirror ``default.yaml`` one-to-one. Prompt templates and the enabled
tool-name list have **no default** — they must come from YAML — while scalar
fields carry a pydantic default that matches the YAML value (so a bare
``Model()`` / ``Agent()`` in tests still works).
"""

import os
from typing import Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)


# ``Model.query`` supplies these parameters from its call-site.  Allowing a
# config value to provide them would either raise a duplicate-keyword error
# (``model``/``messages``) or silently change which tools are exposed
# (``tools``), so they are intentionally not configurable through
# ``model_kwargs``.
_QUERY_RESERVED_KWARGS: Final = frozenset({"model", "messages", "tools"})


class ModelConfig(BaseModel):
    """Model provider settings.  ``api_key_env`` names the environment
    variable that holds the secret — never the secret itself."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    model_kwargs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_name", "api_key_env")
    @classmethod
    def names_are_non_empty(cls, value: str, info) -> str:
        """Reject blank identifiers before a client is created.

        A whitespace-only model name or environment variable name otherwise
        survives YAML parsing and fails much later with an opaque provider or
        ``KeyError`` exception.
        """
        if not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @field_validator("model_kwargs")
    @classmethod
    def query_arguments_are_not_overridden(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        blocked = sorted(_QUERY_RESERVED_KWARGS.intersection(value))
        if blocked:
            names = ", ".join(blocked)
            raise ValueError(f"model_kwargs cannot override query arguments: {names}")
        return value


class AgentConfig(BaseModel):
    """Agent-loop behaviour: prompts, context compression, and run limits."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str          # 必填 —— 来自 YAML
    instance_template: str      # 必填 —— 来自 YAML
    summary_prompt: str         # 必填 —— 来自 YAML
    summary_marker: str = "[CONTEXT SUMMARY]"
    context_window: int = Field(default=64000, gt=0)
    # A zero threshold would trigger compression on every turn.  ``1`` is a
    # useful upper bound because it means compress only at the usable limit.
    compress_threshold: FiniteFloat = Field(default=0.8, gt=0, le=1)
    reserve_tokens: int = Field(default=2000, ge=0)
    keep_last_n_turns: int = Field(default=4, ge=0)
    max_steps: int = Field(default=250, gt=0)
    # ``None`` explicitly disables the wall-clock limit; zero is rejected so
    # an accidental zero cannot make every run stop before its first query.
    max_time: FiniteFloat | None = Field(default=1800.0, gt=0)
    # Both ``None`` and zero retain the existing meaning of disabling the
    # budget.  Negative values are never meaningful.
    cost_limit: FiniteFloat | None = Field(default=3.0, ge=0)
    no_tool_call_retries: int = Field(default=0, ge=0)
    # When set, the first valid submit call is treated as a draft. The agent
    # receives this prompt and must submit again to finish the run.
    submission_review_prompt: str | None = None
    # A clean review context reduces anchoring on the draft author's prior
    # rationale while the append-only event journal retains the full history.
    submission_review_reset_context: bool = False

    @field_validator("submission_review_prompt")
    @classmethod
    def submission_review_prompt_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("submission_review_prompt must be non-empty when set")
        return value

    @model_validator(mode="after")
    def reserve_fits_context_window(self) -> "AgentConfig":
        if self.reserve_tokens >= self.context_window:
            raise ValueError("reserve_tokens must be less than context_window")
        return self


class ToolsConfig(BaseModel):
    """Enabled tool names plus shared execution defaults.

    Tool schemas and handlers live together in ``mini_agent.tools``' explicit
    registry.  Configuration only selects a subset, so model visibility and
    execution permission always come from the same ``enabled`` list.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: list[str]             # 必填 —— 来自 default.yaml
    default_max_lines: int = Field(default=100, gt=0)
    default_max_chars: int = Field(default=20000, gt=0)
    default_timeout: int = Field(default=30, gt=0)

    @field_validator("enabled")
    @classmethod
    def enabled_names_are_unique(cls, names: list[str]) -> list[str]:
        if not names:
            raise ValueError("at least one tool must be enabled")
        if any(not name.strip() for name in names):
            raise ValueError("enabled tool names must be non-empty")
        if len(names) != len(set(names)):
            raise ValueError("enabled tool names must be unique")
        return names

    def tool_names(self) -> list[str]:
        """Return enabled names in the configured, stable order."""
        return list(self.enabled)


class CostConfig(BaseModel):
    """USD price per 1M tokens."""

    model_config = ConfigDict(extra="forbid")

    price_input_per_1m: FiniteFloat = Field(default=0.0, ge=0)
    price_input_cache_hit_per_1m: FiniteFloat = Field(default=0.0, ge=0)
    price_output_per_1m: FiniteFloat = Field(default=0.0, ge=0)


class EnvironmentConfig(BaseModel):
    """Execution environment settings."""

    model_config = ConfigDict(extra="forbid")

    type: str = "local"             # 工厂用其选环境类
    env: dict[str, str] = Field(default_factory=dict)
    image: str = "python:3.11-slim"
    cwd: str = "/"
    timeout: int = Field(default=30, gt=0)
    container_timeout: str = "2h"
    forward_env: list[str] = Field(default_factory=list)
    executable: str = Field(default_factory=lambda: os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker"))
    run_args: list[str] = Field(default_factory=lambda: ["--rm"])
    pull_timeout: int = Field(default=120, gt=0)
    interpreter: list[str] = Field(default_factory=lambda: ["bash", "-lc"])
    block_network_commands: bool = False

    @field_validator("interpreter")
    @classmethod
    def interpreter_is_non_empty(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("interpreter must contain at least one non-empty command")
        return value


class RunConfig(BaseModel):
    """Cross-component options used by benchmark runners."""

    model_config = ConfigDict(extra="forbid")

    env_startup_command: str | None = None


class Config(BaseModel):
    """The full configuration, one section per component."""

    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = Field(default_factory=ModelConfig)
    agent: AgentConfig
    tools: ToolsConfig
    cost: CostConfig = Field(default_factory=CostConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    run: RunConfig = Field(default_factory=RunConfig)

"""Typed configuration models (pydantic v2).

These mirror ``default.yaml`` one-to-one. Prompt templates and the enabled
tool-name list have **no default** — they must come from YAML — while scalar
fields carry a pydantic default that matches the YAML value (so a bare
``Model()`` / ``Agent()`` in tests still works).
"""

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelConfig(BaseModel):
    """Model provider settings.  ``api_key_env`` names the environment
    variable that holds the secret — never the secret itself."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    model_kwargs: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Agent-loop behaviour: prompts, context compression, and run limits."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str          # 必填 —— 来自 YAML
    instance_template: str      # 必填 —— 来自 YAML
    summary_prompt: str         # 必填 —— 来自 YAML
    summary_marker: str = "[CONTEXT SUMMARY]"
    context_window: int = 64000
    compress_threshold: float = 0.8
    reserve_tokens: int = 2000
    keep_last_n_turns: int = 4
    max_steps: int = 250
    max_time: float = 1800.0
    cost_limit: float = 3.0
    no_tool_call_retries: int = 0


class ToolsConfig(BaseModel):
    """Enabled tool names plus shared execution defaults.

    Tool schemas and handlers live together in ``mini_agent.tools``' explicit
    registry.  Configuration only selects a subset, so model visibility and
    execution permission always come from the same ``enabled`` list.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: list[str]             # 必填 —— 来自 default.yaml
    default_max_lines: int = 100
    default_timeout: int = 30

    @field_validator("enabled")
    @classmethod
    def enabled_names_are_unique(cls, names: list[str]) -> list[str]:
        if not names:
            raise ValueError("at least one tool must be enabled")
        if any(not name for name in names):
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

    price_input_per_1m: float = 0.0
    price_input_cache_hit_per_1m: float = 0.0
    price_output_per_1m: float = 0.0


class EnvironmentConfig(BaseModel):
    """Execution environment settings."""

    model_config = ConfigDict(extra="forbid")

    type: str = "local"             # 工厂用其选环境类
    env: dict[str, str] = Field(default_factory=dict)
    image: str = "python:3.11-slim"
    cwd: str = "/"
    timeout: int = 30
    container_timeout: str = "2h"
    forward_env: list[str] = Field(default_factory=list)
    executable: str = Field(default_factory=lambda: os.getenv("MSWEA_DOCKER_EXECUTABLE", "docker"))
    run_args: list[str] = Field(default_factory=lambda: ["--rm"])
    pull_timeout: int = 120
    interpreter: list[str] = Field(default_factory=lambda: ["bash", "-lc"])


class RunConfig(BaseModel):
    """Cross-component options used by benchmark runners."""

    model_config = ConfigDict(extra="forbid")

    env_startup_command: str | None = None


class Config(BaseModel):
    """The full configuration, one section per component."""

    model_config = ConfigDict(extra="forbid")

    model: ModelConfig = ModelConfig()
    agent: AgentConfig
    tools: ToolsConfig
    cost: CostConfig = CostConfig()
    environment: EnvironmentConfig = EnvironmentConfig()
    run: RunConfig = RunConfig()

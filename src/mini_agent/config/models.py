"""Typed configuration models (pydantic v2).

These mirror ``default.yaml`` one-to-one.  Prompt templates and tool schemas
have **no default** — they must come from YAML — while scalar fields carry a
pydantic default that matches the YAML value (so a bare ``Model()`` /
``Agent()`` in tests still works).
"""

from typing import Any

from pydantic import BaseModel


class ModelConfig(BaseModel):
    """Model provider settings.  ``api_key_env`` names the environment
    variable that holds the secret — never the secret itself."""

    model_name: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"


class AgentConfig(BaseModel):
    """Agent-loop behaviour: prompts, context compression, and run limits."""

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


class ToolsConfig(BaseModel):
    """Tool schemas are passed through to the OpenAI API verbatim.

    ``bash_tool`` / ``submit_tool`` are always present. ``read_tool`` /
    ``edit_tool`` / ``write_tool`` are optional and enabled by default in
    ``default.yaml``; ``default_bash.yaml`` sets them to ``null`` to restore
    the legacy bash+submit-only route.
    """

    bash_tool: dict[str, Any]       # 必填 —— 来自 default.yaml
    submit_tool: dict[str, Any]     # 必填 —— 来自 default.yaml
    read_tool: dict[str, Any] | None = None
    edit_tool: dict[str, Any] | None = None
    write_tool: dict[str, Any] | None = None
    default_max_lines: int = 100
    default_timeout: int = 30

    def enabled_tools(self) -> list[dict[str, Any]]:
        """The tool schemas actually sent to the model, in a stable order."""
        tools = [self.bash_tool, self.submit_tool]
        for extra in (self.read_tool, self.edit_tool, self.write_tool):
            if extra is not None:
                tools.append(extra)
        return tools

    def tool_names(self) -> list[str]:
        return [t["function"]["name"] for t in self.enabled_tools()]


class CostConfig(BaseModel):
    """USD price per 1M tokens."""

    price_input_per_1m: float = 0.14
    price_input_cache_hit_per_1m: float = 0.0028
    price_output_per_1m: float = 0.28


class EnvironmentConfig(BaseModel):
    """Execution environment settings."""

    type: str = "local"             # 工厂用其选环境类
    env: dict[str, str] = {}
    image: str = "python:3.11-slim"
    cwd: str = "/"
    timeout: int = 30
    container_timeout: str = "2h"


class Config(BaseModel):
    """The full configuration, one section per component."""

    model: ModelConfig = ModelConfig()
    agent: AgentConfig
    tools: ToolsConfig
    cost: CostConfig = CostConfig()
    environment: EnvironmentConfig = EnvironmentConfig()

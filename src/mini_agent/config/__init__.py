"""Configuration loading, merging, and template rendering.

This package replaces the old flat ``config.py`` with the reference
project's core pattern:

* **``recursive_merge`` + ``UNSET``** — merge many dict layers with
  "last one wins", skipping ``UNSET`` values.
* **Jinja2 template rendering** — prompts live in YAML as ``{{ task }}``
  templates and are rendered at runtime with ``StrictUndefined``.

Precedence (lowest → highest)::

    builtin ``default.yaml``  <  ``--config`` specs (left→right)
    <  ``MINI_AGENT_*`` env vars  <  CLI flags

This module imports only stdlib + yaml/jinja2/pydantic — **never** any
``mini_agent`` component — so it cannot cause circular imports.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, Template

from mini_agent.config.models import (  # noqa: F401
    AgentConfig,
    Config,
    CostConfig,
    EnvironmentConfig,
    ModelConfig,
    RunConfig,
    ToolsConfig,
)

builtin_config_dir = Path(__file__).parent

UNSET = object()
"""Sentinel for "not specified".  ``recursive_merge`` skips these keys."""


def recursive_merge(*dictionaries: dict | None) -> dict:
    """Merge dictionaries recursively — later ones win, ``UNSET`` is skipped.

    Nested dicts are merged recursively rather than replaced, so a layer can
    override ``agent.max_steps`` without wiping the rest of ``agent``.
    """
    if not dictionaries:
        return {}
    result: dict[str, Any] = {}
    for d in dictionaries:
        if d is None:
            continue
        for key, value in d.items():
            if value is UNSET:
                continue
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = recursive_merge(result[key], value)
            elif isinstance(value, dict):
                # Recursively merge so nested UNSET values are filtered out.
                result[key] = recursive_merge(value)
            else:
                result[key] = value
    return result


def get_config_path(config_spec: str | Path) -> Path:
    """Resolve a config spec to a ``.yaml`` path.

    Tries the path as given (appending ``.yaml`` when missing), then the
    builtin config directory.
    """
    config_spec = Path(config_spec)
    if config_spec.suffix != ".yaml":
        config_spec = config_spec.with_suffix(".yaml")
    candidates = [
        Path(config_spec),
        builtin_config_dir / config_spec,
        builtin_config_dir / "benchmarks" / config_spec,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Could not find config file for {config_spec} (tried: {candidates})"
    )


def _key_value_spec_to_nested_dict(config_spec: str) -> dict:
    """Interpret a dotted ``key=value`` spec into a nested dict.

    ``"agent.max_steps=500"`` → ``{"agent": {"max_steps": 500}}``.
    The value is JSON-decoded when possible (``500`` → int, ``"x"`` → str).
    """
    key, value = config_spec.split("=", 1)
    try:
        value = json.loads(value)
    except json.JSONDecodeError:
        pass
    keys = key.split(".")
    if any(k == "" for k in keys):
        raise ValueError(f"Invalid config spec {config_spec!r}: empty config key")
    result: dict[str, Any] = {}
    current = result
    for k in keys[:-1]:
        current[k] = {}
        current = current[k]
    current[keys[-1]] = value
    return result


def get_config_from_spec(config_spec: str | Path) -> dict:
    """Load a config layer from a ``key=value`` spec or a YAML file path."""
    if isinstance(config_spec, str) and "=" in config_spec:
        return _key_value_spec_to_nested_dict(config_spec)
    path = get_config_path(config_spec)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _env_var_overrides(prefix: str = "MINI_AGENT_") -> dict:
    """Collect ``MINI_AGENT_*`` env vars into a nested dict.

    ``MINI_AGENT_AGENT__MAX_STEPS=500`` → ``{"agent": {"max_steps": 500}}``.
    ``__`` is the nesting separator.  Values are JSON-decoded when possible.
    Secrets like ``OPENAI_API_KEY`` have no prefix, so they are ignored.
    """
    result: dict[str, Any] = {}
    for name, value in os.environ.items():
        if not name.startswith(prefix):
            continue
        key = name[len(prefix):].lower()
        keys = key.split("__")
        if any(k == "" for k in keys):
            continue
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        current = result
        for k in keys[:-1]:
            current = current.setdefault(k, {})
        current[keys[-1]] = value
    return result


def load_default_yaml() -> dict:
    """Read the builtin ``default.yaml`` (authoritative defaults)."""
    return yaml.safe_load(
        (builtin_config_dir / "default.yaml").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=None)
def get_default_config() -> Config:
    """The default ``Config``, parsed from ``default.yaml`` once and cached."""
    return Config.model_validate(load_default_yaml())


def build_config(
    config_specs: list[str | Path] | None = None,
    cli_overrides: dict | None = None,
    *,
    env_prefix: str = "MINI_AGENT_",
) -> Config:
    """Merge all config sources into a validated ``Config``.

    ``config_specs`` is a list of ``--config`` values (YAML paths or
    ``key=value``); ``cli_overrides`` is a nested dict from CLI flags where
    "not given" is ``UNSET`` so it never clobbers a lower layer.
    """
    layers = [load_default_yaml()]
    for spec in config_specs or []:
        layers.append(get_config_from_spec(spec))
    layers.append(_env_var_overrides(env_prefix))
    layers.append(cli_overrides or {})
    return Config.model_validate(recursive_merge(*layers))


def render_template(template: str, **variables: Any) -> str:
    """Render a Jinja2 ``{{ var }}`` template.

    ``StrictUndefined`` raises :class:`jinja2.UndefinedError` on a missing
    variable instead of silently rendering an empty string.
    """
    return Template(template, undefined=StrictUndefined).render(**variables)


__all__ = [
    "AgentConfig",
    "Config",
    "CostConfig",
    "EnvironmentConfig",
    "ModelConfig",
    "RunConfig",
    "ToolsConfig",
    "UNSET",
    "build_config",
    "builtin_config_dir",
    "get_config_from_spec",
    "get_config_path",
    "get_default_config",
    "load_default_yaml",
    "recursive_merge",
    "render_template",
    "_env_var_overrides",
    "_key_value_spec_to_nested_dict",
]

# Configuration Reference

This page lists field responsibilities by pydantic model without duplicating defaults that may drift.
For current defaults, see [`default.yaml`](../../src/mini_agent/config/default.yaml) and
[`swebench.yaml`](../../src/mini_agent/config/benchmarks/swebench.yaml) directly. The authoritative
source for types and constraints is [`config/models.py`](../../src/mini_agent/config/models.py).

## Top-Level Config

| section | Model | Consumers |
|---|---|---|
| `model` | `ModelConfig` | Model, benchmark model factory |
| `agent` | `AgentConfig` | Agent, context, prompt rendering |
| `tools` | `ToolsConfig` | registry selection, handlers |
| `cost` | `CostConfig` | `compute_cost()` |
| `environment` | `EnvironmentConfig` | factory, Local/Docker, network policy |
| `run` | `RunConfig` | benchmark runner |

All models use `extra="forbid"`: unknown fields are configuration errors and are not silently
ignored.

## model

| Field | Type | Meaning |
|---|---|---|
| `model_name` | `str` | Model identifier sent to the provider; cannot be empty |
| `base_url` | `str \| null` | OpenAI-compatible endpoint; null uses the SDK default |
| `api_key_env` | `str` | Name of the environment variable holding the key; cannot be empty |
| `model_kwargs` | object | Additional arguments passed unchanged to Chat Completions create |

`model_kwargs` cannot override `model`, `messages`, or `tools`, because those are controlled by the
call site. The actual API key is not a configuration field.

## agent

| Field | Type/constraint | Meaning |
|---|---|---|
| `system_prompt` | required string | First system message |
| `instance_template` | required string | Renders the user message with `task` |
| `summary_prompt` | required string | Renders a summary request with `existing_summary` and `new_lines` |
| `summary_marker` | string | Marks a compressed summary in messages |
| `context_window` | positive int | Agent's own estimated window, not a provider declaration |
| `compress_threshold` | `(0, 1]` finite float | Fraction of the available window that triggers compression |
| `reserve_tokens` | nonnegative int | Reserved for the next output; must be less than the context window |
| `keep_last_n_turns` | nonnegative int | Number of recent atomic units retained verbatim after compression |
| `max_steps` | positive int | Limit on model decision rounds in the main loop |
| `max_time` | positive float or null | Total wall-clock limit; null disables it |
| `cost_limit` | nonnegative float or null | Cumulative USD limit; 0/null disables it |
| `no_tool_call_retries` | nonnegative int | Number of corrections after a response with no tool calls |
| `submission_review_prompt` | nonblank string or null | Review instruction after the first submit |
| `submission_review_reset_context` | bool | Whether review resets the author's messages |
| `submission_review_checkpoint_context` | bool | Whether to include evidence and an untrusted summary |

When checkpointing is enabled, the review prompt must exist and reset must be true.

## tools

| Field | Type/constraint | Meaning |
|---|---|---|
| `enabled` | nonempty, unique string list | Selects schemas and execution permissions in order |
| `default_max_lines` | positive int | Default line budget for bash/read |
| `default_max_chars` | positive int | Character budget for tool observations |
| `default_timeout` | positive int | Default bash timeout |

Enabled names must also appear in `TOOL_REGISTRY`; this check occurs when schemas are retrieved.

## cost

| Field | Unit |
|---|---|
| `price_input_per_1m` | USD per million uncached input tokens |
| `price_input_cache_hit_per_1m` | USD per million cache-hit input tokens |
| `price_output_per_1m` | USD per million output tokens |

All three are finite, nonnegative floating-point values. No provider prices are assumed by default;
check the current prices before a batch run.

## environment

| Field | Meaning |
|---|---|
| `type` | Factory name, currently local/docker |
| `env` | String key/value pairs injected into the command environment |
| `image` | Docker image |
| `cwd` | Working directory for container commands and file tools |
| `timeout` | Environment's default command timeout |
| `container_timeout` | Duration expression for keeping a long-lived container alive |
| `forward_env` | Environment variable names forwarded from the host into the container |
| `executable` | Docker-compatible CLI path/command |
| `run_args` | Additional arguments passed to container run |
| `pull_timeout` | Image-pull timeout |
| `interpreter` | Nonempty string-list argv used to execute commands in the container |
| `block_network_commands` | Whether the tool layer rejects obvious network commands |

`block_network_commands` is not a sandbox; use container network configuration when a hard network
boundary is required.

## run

`env_startup_command` is an optional Jinja2 string. The benchmark renders it with instance fields and
executes it after the environment is created and before Agent starts; a nonzero structured return code
makes startup fail and triggers cleanup.

## Merge Rules

`recursive_merge` recursively merges nested mappings, with later values winning. `UNSET` means that a
layer did not specify a value and therefore does not override a lower layer. `None` is a valid value
and genuinely overrides one, for example to disable `max_time`.

A configuration spec can be a YAML path, a built-in name, or dotted `key=value`. The default
environment-variable prefix is `MINI_AGENT_`, with `__` separating levels. See the [Configuration
Guide](../guides/configuration.md) for usage examples.

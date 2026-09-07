# Configuration Guide

The configuration is designed to make prompts, budgets, providers, tools, and environment policies
composable while keeping secrets in the runtime environment. This page explains how to use it; see
the [Configuration Reference](../reference/configuration.md) for field definitions.

## Configuration Sources

The final `Config` is built by recursively merging four layers, with later values taking precedence:

```text
built-in default.yaml
  < --config file or dotted key=value (left to right)
  < MINI_AGENT_* environment variables
  < dedicated CLI arguments
```

The ordinary default file is
[`default.yaml`](../../src/mini_agent/config/default.yaml). The SWE-bench CLI additionally overlays
[`benchmarks/swebench.yaml`](../../src/mini_agent/config/benchmarks/swebench.yaml) first.

## Using a YAML File

Write only the nested fields you want to override:

```yaml
model:
  model_name: my-model
  base_url: https://provider.example/v1

agent:
  max_steps: 80

cost:
  price_input_per_1m: 1.0
  price_input_cache_hit_per_1m: 0.1
  price_output_per_1m: 4.0
```

Run it with:

```bash
minimal --config my-provider.yaml --task "inspect the project"
```

`--config` can be repeated. Later files or key=value entries override earlier ones without erasing
unmentioned fields at the same level.

## Overriding a Single Field

```bash
minimal -c agent.max_steps=50 -c environment.type=docker
minimal -c 'tools.enabled=["bash","submit"]'
minimal -c agent.cost_limit=null
```

Values are first parsed as JSON: numbers, booleans, null, arrays, and objects retain their types;
ordinary unquoted text becomes a string. Keys may not contain empty segments.

Environment variables express nesting with double underscores:

```bash
MINI_AGENT_AGENT__MAX_STEPS=50 minimal
MINI_AGENT_ENVIRONMENT__BLOCK_NETWORK_COMMANDS=true minimal
```

Dedicated CLI arguments such as `--max-steps`, `--env`, and `--image` have the highest priority.
Flags that are not passed use the internal `UNSET` sentinel and cannot accidentally overwrite a YAML
value with `None`.

## Protecting API Keys

`model.api_key_env` in YAML is the name of an environment variable, not the secret itself:

```yaml
model:
  api_key_env: OPENAI_API_KEY
```

Put the actual value in an uncommitted `.env` file or the process environment:

```bash
OPENAI_API_KEY=... minimal --task "inspect the project"
```

Do not put the key in `model_kwargs`, a trajectory, command arguments, or a committable profile.
Docker's `forward_env` should also be minimal; a model provider key usually only needs to remain in
the host Model and should not enter the execution container.

## Prompt Templates

`agent.instance_template` and `summary_prompt` use Jinja2 `{{ variable }}`. Rendering enables
`StrictUndefined`, so a misspelled variable fails immediately instead of silently generating a
prompt with missing fields.

When customizing the system or instance prompt, still retain:

- the allowed working directory and security policy;
- tool-use and final-submit conventions;
- the requirement to check return codes for test results;
- the benchmark restriction against retrieving upstream answers or modifying tests.

## Tool Profiles

The default profile enables bash, submit, read, edit, and write. For a bash-only teaching comparison,
you can overlay:

```bash
minimal --config default_bash --task "inspect the project"
```

`tools.enabled` controls both the schemas sent to the model and dispatcher permissions. Every enabled
name must be registered in the registry; the list cannot be empty or contain duplicates.

## Review Configuration

After `submission_review_prompt` is set, the first submit becomes a draft. To enable clean-context
review:

```yaml
agent:
  submission_review_prompt: |
    Independently inspect the patch and tests, then submit again.
  submission_review_reset_context: true
  submission_review_checkpoint_context: true
```

The checkpoint depends on both the prompt and reset settings; validation fails if either is missing.
It makes an additional summary request, which must be included in cost and time estimates.

## Validating Configuration

After all layers are merged, pydantic `Config` validates the result: unknown fields are rejected, and
constraints cover numeric ranges, the tool list, reserved model kwargs, and review combinations. After
modifying the built-in YAML, run:

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/test_config.py tests/test_config_loading.py \
  tests/test_config_read_edit.py -q -p no:anyio
```

## Common Problems

- `KeyError: OPENAI_API_KEY`: set the environment variable named by `api_key_env`;
- configuration appears ineffective: check whether an environment variable or dedicated CLI flag overrides it with higher priority;
- a string is parsed as a number/boolean: use JSON quotes in key=value;
- the cost limit does not stop a run: configure real, nonzero prices for the current provider;
- Docker options affect Local: most Docker fields are ignored by Local, a consequence of sharing the model configuration.

Return to the [Documentation Home](../index.md).

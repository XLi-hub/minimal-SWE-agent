# Models and execution environments

Both Model and Environment are replaceable, but their contracts have different strengths: the
repository's `Model` is a concrete OpenAI-compatible adapter used by Agent through duck typing,
whereas Environment is an explicit ABC.

## Model: concrete adapter and duck-typing boundary

[`model.py`](../../src/mini_agent/model.py) contains `Model`:

1. reads the model name, base URL, secret environment-variable name, and extra parameters from `ModelConfig`;
2. constructs the OpenAI client;
3. sends messages and optional tools through Chat Completions `create(...)`;
4. returns the provider response unchanged for Agent to parse its choice and usage;
5. releases HTTP connections with `close()`.

It is not an abstract base class, and the project has no `ModelProtocol`. `Agent` only assumes that
the object provides:

```python
model.query(messages, tools=None) -> OpenAI-compatible response
```

Unit tests can therefore inject a fake or mock as long as the response contains a compatible
`choices[0].message` and the required usage. Here, a "replaceable model" means duck typing and an
OpenAI-compatible response; it does not mean that every provider needs no adaptation.

The built-in adapter sets `supports_request_timeout = True` and accepts an optional `timeout` keyword.
Agent uses it to cap each request to the remaining run time. Third-party adapters can implement the
same opt-in contract; adapters without it retain the smaller two-argument query surface.

The Model secret does not enter configuration values; `api_key_env` stores only the environment
variable name. Users configure the provider's actual model parameters, base URL, and prices; see the
[configuration reference](../reference/configuration.md) for the authoritative fields.

## Cost accounting

[`cost.py`](../../src/mini_agent/cost.py) reads input, cached-input, and output tokens from response
usage and calculates cost using prices per million tokens. Main queries and compression summaries
use the same accounting function.

The default YAML prices are zero because compatible providers have different billing models. This
means that the default `cost_limit` value alone cannot prevent real charges; a cumulative dollar cap
has meaning only after non-zero, correct prices are configured.

## Environment: explicit ABC

[`environments/base.py`](../../src/mini_agent/environments/base.py) defines:

```python
execute(command, timeout) -> ExecutionResult
read_file(path) -> str
write_file(path, content) -> None
cleanup() -> None
```

The first three methods are abstract; `cleanup()` has a no-op default implementation, which
subclasses holding external resources should override. `ExecutionResult` is a mapping with the
standard keys `output`, `returncode`, and `exception_info`.

A command's non-zero exit is a normal observation recorded in `returncode`; startup failures,
timeouts, or runner errors use `returncode=-1` and `exception_info`. Environment captures combined
stdout/stderr, while the tool layer formats and truncates it.

## LocalEnvironment

Local starts subprocesses through the host shell and inherits the current Python process's working
directory. File tools resolve paths relative to the same working directory, so bash and read/edit/write
see the same tree. Ordinary host environment variables are inherited, but protected variables such as
the configured provider API key are removed unless explicitly opted in through `forward_env`.

On POSIX, commands run in an independent session/process group. On timeout, the implementation
terminates the entire process group and collects output, avoiding orphaned child processes when only
the shell is killed. Windows uses the corresponding process-tree termination fallback.

The security implication is direct: the model can run commands the current user is authorized to
run, read data, and write files. Local mode is not a sandbox, and output truncation does not limit a
command's own permissions.

## DockerEnvironment

Docker ensures that the image is available during initialization, starts a long-lived container,
and then runs multiple commands through `docker exec`. This preserves file changes inside the
container while avoiding container recreation for every tool call.

File I/O also happens through commands inside the container, with paths relative to the configured
`cwd`. The interpreter, container lifetime, image-pull timeout, `run_args`, and limited environment
variable forwarding are controlled by EnvironmentConfig.

`cleanup()` stops and removes the container; call it from `finally`. Docker daemon permissions are
typically high, and an incorrect mount, privileged argument, or forwarded secret can still undermine
the isolation assumptions.

## Factory and dependency injection

`get_environment(name, **kwargs)` selects `local` or `docker` through a registered mapping. The CLI
first constructs typed config and then passes `config.environment.type` to the factory. Agent has no
branch on environment names.

When adding an environment:

1. inherit from `Environment` and implement the three abstract operations;
2. implement an idempotent, or at least safe, `cleanup()` for any external resources acquired;
3. register the name in the factory mapping;
4. add tests for construction, command results, file consistency, timeouts, and cleanup;
5. if new configuration fields are needed, update the pydantic model and YAML.

## Resource ownership

The ordinary CLI creates Model and Environment, so `finally` calls `model.close()` first and then
`environment.cleanup()`; cleanup exceptions only produce warnings and do not mask the actual run
exception.

Each SWE-bench instance owns its own model and container; resources must be released whether
construction fails partway through, Agent fails, or the run succeeds. If users construct objects
directly, the library does not infer their lifecycle; callers should clean them up explicitly.

## Security checklist

- Local: use only for trusted tasks and disposable worktrees;
- Docker: inspect the image source, mounts, run args, cwd, and forwarded environment variables;
- Network: a command-level blocklist is not a boundary; container network policy is;
- Secrets: the provider key is withheld from Local commands by default, and trajectory snapshots
  redact `environment.env` values;
- Timeout: terminates the child-process tree but cannot roll back side effects that have occurred;
- Cleanup: releases resources; it does not undo file modifications.

Related pages: [Architecture overview](overview.md), [Tool system](tool-system.md),
[Design trade-offs](../decisions/design-tradeoffs.md).

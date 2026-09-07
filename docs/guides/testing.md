# Testing Guide

Tests are layered by risk boundary: pure functions and fakes come first, a real shell verifies
integration, Docker verifies the resource layer, and the real model API runs only in explicit E2E
tests. Test counts change, so this document does not hard-code them.

## Default Command

The repository requires the project conda environment:

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio -m "not e2e"
```

This command does not select real-provider E2E tests. If CI also lacks a Docker daemon, exclude Docker
tests as well:

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio \
  -m "not e2e and not docker"
```

The pytest default `addopts` in `pyproject.toml` also excludes `e2e`, but the repository convention is
to use the explicit command above so that third-party pytest plugins on the machine cannot change its
behavior.

## Pure Unit Layer

This layer does not access the network or run real commands. It covers:

- Agent budgets, exits, batch confirmation, and the review state machine;
- context grouping, compression, and token estimation;
- config merging, environment variables, templates, and pydantic validation;
- tool schemas/handlers, file editing, and output truncation;
- billing, evidence extraction, and persistence;
- dataset and prediction storage, and harness command construction.

A fake Model should return a structurally compatible response rather than bypassing Agent's parsing
path. A fake Environment should return an `ExecutionResult`-shaped value and, when needed, record
calls so tests can assert that no side effects occur after a budget is reached.

## Integration Layer

Tests such as `tests/test_integration.py` combine a real `LocalEnvironment` with a fake Model to verify:

- shell quoting, stdout/stderr, and empty output;
- that multiple rounds of assistant/tool messages are consumed correctly by the next query;
- that a nonzero return code is not confused with an execution error;
- that file tools and bash observe the same host working directory;
- that no child-process tree remains after a timeout.

A real shell exposes problems hidden by mocks, but it still must not access the network or a real model
service.

## Docker Layer

Tests marked `docker` require access to a daemon and verify image startup, cwd, file I/O, environment
forwarding, timeout, and cleanup. When the daemon is unavailable, tests should skip rather than
pretend to pass.

When debugging, first check:

```bash
docker info
```

Docker tests may pull images, consume disk space, and take longer. Do not attribute an ordinary unit
test failure to Docker; narrow the scope with a focused test first.

## E2E Layer

E2E tests call a real OpenAI-compatible API and may incur charges, so run them only after the user has
explicitly decided to do so:

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/test_e2e.py -v -p no:anyio -m e2e
```

Before running, verify the provider, model, base URL, API key, token prices, cost limit, and task
content. The mere presence of a key in the environment does not authorize a run.

## Focused Tests After a Change

Choose the narrowest sufficient set based on the change:

| Change | Preferred tests |
|---|---|
| Agent/submit/review | `tests/test_agent.py`, `tests/test_evidence.py` |
| tools/tooling | `tests/test_tools.py` |
| config/YAML | `tests/test_config*.py` |
| environments | `tests/test_environment.py`, `test_environments_init.py`, `test_docker.py` |
| context/trajectory | `test_context.py`, `test_persistence.py` |
| benchmark | `tests/benchmarks/` |
| CLI/resources | `tests/test_cli.py`, `tests/benchmarks/test_cli.py` |
| docs links | `tests/test_docs.py` |

After focused tests pass, run the complete non-E2E suite before delivery.

## What to Assert

High-value assertions check the observable contract, not only that “no exception was raised”:

- tool call IDs and tool responses correspond one-to-one;
- return codes, exception information, and output are accurate;
- a handler does not execute after a time or cost limit is reached;
- cleanup occurs on both success and failure paths;
- the saved event count matches the sidecar contents;
- the runner's prediction and status can resume from a checkpoint;
- review does not mistake the author's summary for machine evidence.

## Interpreting Failures

The return code of the test command itself determines success. Without `pipefail`, `pytest | tail`
may show the final lines while hiding a pytest failure; the SWE-bench profile therefore enables
pipefail. Missing dependencies, undiscovered tests, timeouts, and skips all need to be reported
accurately and must not be collectively called “tests passed.”

Return to the [Documentation Home](../index.md), or continue with the [Design Trade-offs](../decisions/design-tradeoffs.md).

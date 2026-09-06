# SWE-bench Guide

The SWE-bench workflow has two stages: generation and official evaluation. Confirm the environment
and cost with a single instance first, then expand the slice and worker count; a successful submission
does not mean that the official harness resolves it.

## Installation

Dataset generation uses the optional dependency set:

```bash
conda run -n minimal-SWE-agent pip install -e ".[bench]"
```

Install the separate evaluation extra for official scoring:

```bash
conda run -n minimal-SWE-agent pip install -e ".[eval]"
```

Do not install dependencies into the base conda environment. Before running, you also need a Docker
daemon, enough disk space, and an API key for the target provider.

## Single-Instance Smoke Test

```bash
conda run -n minimal-SWE-agent minimal-swebench \
  --subset verified --split test \
  --instance 0 \
  --model gpt-4o-mini \
  --output runs/verified-smoke
```

`--instance` can be an exact instance ID or a numeric index after sorting IDs. First check that the
container starts successfully, the working directory is `/testbed`, the trajectory/events are saved,
the prediction is a unified diff, and resources are cleaned up.

When the default token prices are zero, the CLI warns that the cost limit cannot take effect in
dollars. Before a full batch run, set the current provider's real prices through a configuration file
or `-c cost...`.

## Selecting Tasks

Built-in aliases include `full`, `verified`, `lite`, `multimodal`, and `multilingual`. They can be
combined:

```bash
minimal-swebench \
  --subset verified --split test \
  --filter 'django__django-' \
  --slice 0:20 \
  --output runs/django-20
```

`--shuffle --seed 42` performs a deterministic shuffle before slicing. Record the alias, actual
dataset, split, filter, slice, seed, and instance IDs so the experiment can be reproduced.

## Parallel Generation

```bash
conda run -n minimal-SWE-agent minimal-swebench \
  --subset verified --split test \
  --slice 0:20 --workers 4 \
  --model gpt-4o-mini \
  --output runs/verified-20
```

Each worker creates its own model, Docker container, Agent, and trajectory. Before increasing
concurrency, assess API rate limits, cost, Docker CPU/memory, image storage, and disk-write pressure.

## Strict Profile

The command overlays
[`swebench.yaml`](../../src/mini_agent/config/benchmarks/swebench.yaml) on the ordinary defaults. Its
key policies include:

- Docker working directory `/testbed`;
- container `--network=none`;
- pre-execution blocking of common network commands;
- Bash `-o pipefail`;
- benchmark-specific budgets and tool timeouts;
- the trajectory tool;
- clean-context draft review and an evidence checkpoint.

This profile receives no hidden tests or evaluator feedback. The reviewer can use only the issue,
repository, currently available tests, candidate patch, and its own event records.

## Output Directory

```text
runs/verified-20/
├── preds.json
├── preds.jsonl
├── statuses.json
└── <instance_id>/
    ├── <instance_id>.traj.json
    └── <instance_id>.events.jsonl
```

`preds.jsonl` is the official harness input. The trajectory instance metadata intentionally excludes
the gold patch, hidden tests, and evaluation script; do not manually restore these fields from the
raw dataset row.

## Resuming a Run

An output directory retains existing terminal results by default. Choose as needed:

```bash
minimal-swebench ... --retry-failed
minimal-swebench ... --redo-existing
```

`--retry-failed` targets failed items, while `--redo-existing` explicitly reruns existing items.
Before rerunning, back up experiment artifacts that must be preserved and record the code commit and
configuration; mixing different versions in a directory with the same name weakens comparability.

## Official Scoring

```bash
conda run -n minimal-SWE-agent minimal-swebench-eval \
  runs/verified-20/preds.jsonl \
  --dataset verified --split test --workers 4 \
  --run-id verified-20 \
  --report-dir runs/verified-20/reports
```

The evaluation adapter runs the official harness and collects a report. Use `--help` for the exact
CLI parameters:

```bash
conda run -n minimal-SWE-agent minimal-swebench-eval --help
```

## Interpreting Results

Report at least the following separately:

1. whether generation was `submitted` and whether it produced a non-empty unified diff;
2. which tests Agent ran, their exact return codes, and their coverage;
3. whether the container, image, and setup worked;
4. whether the official harness completed successfully;
5. whether the final result was resolved;
6. token/API calls/cost, steps, and wall-clock time;
7. whether review changed the patch or added independent verification.

“Local tests pass,” “a diff was submitted,” and “the harness resolved it” are three different kinds of
evidence. When something fails, first distinguish infrastructure, test environment, patch format, and
behavioral-contract problems before attributing the failure to the model.

## Testing Boundaries

Unit tests for the runner and dataset/storage do not need a real API or Docker. Official E2E generation
may incur charges and is not part of the default test suite. See the [Testing Guide](testing.md) for
the repository's complete non-E2E command.

See the [Benchmark Layer](../architecture/benchmark-layer.md) for architecture details and the
[Experiment Index](../experiments/index.md) for the historical review.

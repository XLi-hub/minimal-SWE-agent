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

## Low-Disk Serial Wrapper

When the Docker/containerd partition is small, use the repository wrapper with an explicit list of
instance IDs. It runs one generation, then the official harness for that same instance, before
moving to the next one:

```bash
conda run -n minimal-SWE-agent python scripts/run_swebench_low_disk.py \
  --instances-file instances.txt \
  --subset verified --split test \
  --output runs/verified-low-disk \
  --model deepseek-flash \
  --provider https://api.deepseek.com \
  --api-key-env DEEPSEEK_API_KEY \
  --pre-pull \
  --input-price-per-1m <current-uncached-input-usd> \
  --cache-hit-price-per-1m <current-cache-hit-input-usd> \
  --output-price-per-1m <current-output-usd> \
  --cost-limit 0.50
```

`instances.txt` contains one exact ID per line. `--instance ID` (repeatable) and `.json`/`.jsonl`
files containing IDs or records with `instance_id` are also accepted. The provider and all three
token prices are explicit so the `agent.cost_limit` can be interpreted in current USD; use the
provider's current pricing rather than copying an old experiment value. The stop threshold applies
separately to each generation instance, so `N * cost-limit` is the aggregate stop threshold, not a
hard billing ceiling. Each instance can exceed it by the final completed model request that causes
the Agent to stop. Official evaluation does not make model-provider calls. Actual usage and cost
remain in each trajectory.

The same output directory is a checkpoint. `low_disk_status.json` is updated after every instance;
completed generation/evaluation/cleanup records are skipped on a later invocation. Add
`--retry-failed` to retry failed records or `--redo-existing` to intentionally regenerate existing
predictions. The wrapper always uses one generation worker and passes one instance ID to the
official evaluator, so there is no evaluation fan-out hidden inside the serial loop.

Pre-pulling is disabled by default for compatibility with the ordinary runner. Enable
`--pre-pull` for a low-disk batch when an image may take longer than the core environment's
300-second pull/start timeout; `--pull-timeout` defaults to 1800 seconds and is configurable. The exact image is pulled
serially before generation. A pull failure is written to `low_disk_status.json`, skips both API
generation and official evaluation for that instance, and still enters the cleanup `finally` block.
Use `--no-pre-pull` to make the compatibility default explicit.

After each instance, a `finally` block attempts only
`docker image rm <that-instance-swebench-image>`. Image garbage collection is enabled by
default, but first runs `docker image ls --filter dangling=true --quiet`; if any dangling image is
already present (or the check fails), it warns and skips `docker image prune --force`. Use
`--no-image-prune`/`--no-gc` to disable even this guarded hint. The wrapper never runs
`docker system prune`. Use `--dry-run` to inspect every generation, evaluation, and cleanup command
without starting a model, harness, or Docker command.

The command above is launched by `conda run`; child commands reuse that environment's
`sys.executable` and do not nest another `conda` invocation.

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

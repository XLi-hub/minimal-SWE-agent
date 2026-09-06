# Benchmark layer

The benchmark layer applies the general-purpose Agent to a set of external tasks and manages
recoverable artifacts. It should not put the dataset, storage, official scoring, or evaluation
strategy back into the Agent loop.

## Module boundaries

```text
benchmarks/
├── cli.py                 argument parsing and profile assembly
├── swebench.py            runner, factory injection, and single-instance lifecycle
├── evaluation.py          official harness command adapter and report collection
└── _swebench/
    ├── dataset.py         dataset aliases, filtering, and image resolution
    └── storage.py         concurrency-safe atomic storage for predictions
```

`_swebench/` is the runner's internal support layer, not a new top-level workflow. Public compatible
imports can be exposed by `benchmarks/__init__.py`, while data and storage implementations remain in
the private package.

## Configuration layering

The benchmark CLI uses the ordinary
[`default.yaml`](../../src/mini_agent/config/default.yaml) as its base, then layers on
[`benchmarks/swebench.yaml`](../../src/mini_agent/config/benchmarks/swebench.yaml), and finally
applies the user's `-c/--config` and `--model` options.

The SWE-bench profile expresses evaluation policy: Docker `/testbed`, no network, pipefail, longer
budgets, the trajectory tool, and two-stage submit review. Do not hard-code these differences into
Agent.

## Dataset layer

`_swebench.dataset`：

- maps `full`, `verified`, `lite`, `multimodal`, and `multilingual` to datasets;
- lazily loads the optional `datasets` dependency;
- selects tasks by regex, slice, seeded shuffle, or instance ID;
- resolves the official Docker image name from instance metadata.

Sorting, slicing, and shuffling must be reproducible. A single `--instance` accepts an exact ID or
a numeric index after sorting, allowing the environment, model, and cost settings to be validated
first.

## Single-instance lifecycle

```text
instance
  ├─ model_factory(config, instance)
  ├─ environment_factory(instance, image, config)
  │    └─ optional run.env_startup_command
  ├─ agent_factory(model, environment, config, instance)
  ├─ Agent.run(problem_statement, trajectory path)
  ├─ collect_model_patch(submission or git diff)
  └─ finally: close model + cleanup environment
```

Factories support keyword arguments and also accommodate the common positional signatures of small
test lambdas. The default factory lazily imports Model/Agent, so merely importing the runner does
not require an API key or start a container.

If environment startup fails, any environment already acquired must still be cleaned up. Each
concurrent instance has its own model, container, and trajectory directory; mutable Agent state
must not be shared.

## Artifacts

An output directory usually contains:

```text
preds.json                  standard predictions keyed by instance_id
preds.jsonl                 official harness input
statuses.json               recoverable instance statuses
<instance>/<instance>.traj.json
<instance>/<instance>.events.jsonl
```

Predictions use the standard fields `model_name_or_path`, `instance_id`, and `model_patch`. The
storage layer protects in-process concurrent updates with a lock and atomically writes through a
temporary file followed by replace.

`statuses.json` is runner recovery state, updated by `swebench.py` during the same instance
lifecycle. It is not folded into prediction storage because the two have different formats and
consumers.

Trajectory instance metadata may contain only public task and setup fields. Do not persist the full
dataset row as a convenience: it may contain a gold patch, hidden tests, or evaluator scripts,
which would both contaminate learning material and violate the evaluation boundary.

## Recovery semantics

- skip existing terminal results by default;
- `--redo-existing` explicitly reruns existing instances;
- `--retry-failed` retries failed statuses only;
- a failed instance in a batch must not corrupt other atomically saved results.

Concurrency mainly improves throughput, while also increasing API costs, image/disk pressure, and
Docker resource usage. Run a single instance first to confirm trajectory, patch, and cleanup
behavior before increasing the worker count.

## Patch collection

The ideal submission is a complete unified diff. If the submission is not a diff, the runner can
collect actual worktree changes by executing `git diff --binary --no-ext-diff` in the environment.
Empty patches, non-submitted terminal states, or execution failures must preserve their true status;
the instance cannot be called solved merely because a prediction row was generated.

## Official scoring

`evaluation.py` constructs the SWE-bench harness command, validates the run ID, runs the subprocess,
and reads the report. Scoring is a separate phase:

```text
Agent submitted ──does not imply── patch valid ──does not imply── harness resolved
```

Network, image, container startup, dependency, and harness failures must be attributed separately
from model patch failures. Experiment reports should record both the generation exit status and the
official resolved result.

## Security and fairness

SWE-bench containers use `--network=none` to block external retrieval; bash network-command
detection is only supplementary. `pipefail` ensures that `pytest | tail` cannot hide a test failure
with tail's zero status. Review may rely only on the issue, repository, available tests, and its own
trajectory; it does not receive hidden evaluator feedback.

See the [SWE-bench guide](../guides/swebench.md) for usage instructions and the
[experiment index](../experiments/index.md) for historical experiments.

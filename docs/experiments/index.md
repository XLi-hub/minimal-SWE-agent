# Experiment Index

This directory contains SWE-bench experiments with explicit dates and sample context. They explain the failures and design changes observed at the time; they do not serve as claims about the current API, defaults, or leaderboard. When reading them, compare them with the current [architecture](../architecture/overview.md) and [SWE-bench guide](../guides/swebench.md).

## Recommended order

1. [DeepSeek V4.1 Flash 100-instance run](swebench-verified-deepseek-v41-flash-100.md): a completed medium-scale run with initial/final scoring, retry attribution, cost accounting, and checksummed artifacts;
2. [Verified two-instance retrospective](swebench-verified-deepseek-v4-retrospective.md): identify contamination, trajectory, and pipeline risks in an apparently successful 2/2 result;
3. [Hard two-instance 0/2 analysis](swebench-verified-hard-0-of-2-analysis.md): why green self-tests still failed to restore the implicit data-model and AST contracts;
4. [Audit rerun](swebench-verified-hard-audit-rerun.md): draft audit improved the process but did not turn the result into a success;
5. [Clean-review follow-up](swebench-clean-review-followup.md): how de-anchoring, trajectory review, and harness incidents should be attributed in separate layers.

## Common themes

- `submitted` only means that the Agent accepted a result;
- passing local tests covers only the inputs and assertions that actually ran;
- an official harness `resolved` result is independent evidence and may also be affected by infrastructure;
- the assistant's self-assessment is not evidence;
- complete events, return codes, patches, and reports should be preserved together;
- small, selected, or known-contaminated samples cannot support broad capability claims.

## Mapping to current mechanisms

| Experiment question | Current code location | Description |
|---|---|---|
| Compression hides original history | `context.py` + `persistence.py` | Two views: messages/events |
| Author conclusions contaminate review | `evidence.py` | Machine-fact checkpoint |
| Clean review loses exploration history | `trajectory` tool | Bounded, on-demand lookup |
| Pipeline false success | SWE-bench YAML interpreter | Bash `pipefail` |
| Contamination from external retrieval | Docker run args + network policy | `network none` is the actual boundary |
| Gold fields enter the trajectory | Benchmark metadata allowlist | Retain only public fields |
| Runner is too centralized | `benchmarks/_swebench/` | Move dataset/storage downward |

## How to cite an experiment

When reporting a conclusion, include at least: code version, configuration profile, model/provider, instance ID, generation status, complete patch, exact test command and return code, harness version, and final report. If any item is missing, explicitly describe it as a limitation.

Model capabilities, provider context windows, prices, and external benchmark status in experiment records may have changed. Use the YAML configuration and the provider's official information for the current runtime configuration.

## Report template

When adding an experiment record, the following minimal structure is recommended:

```text
Question: Which single variable changed this time, and what hypothesis is it intended to falsify?
Setup: commit, profile diff, provider/model, instance selection, seed
Generation: exit status, steps, API calls, cost, wall time
Evidence: command, return code, test inputs and assertions, final patch
Evaluation: harness command/version, infrastructure status, resolved report
Conclusion: What does the observation support, and what does it not support?
Next step: Which variables remain fixed, and what is the only change for the next round?
```

Do not paste only the model's self-report or the last few lines of test output. For pipeline commands, record the `pipefail` state; for timeouts, record whether the process tree was terminated; for reviews, record the author-stage checkpoint summary and the reviewer-stage call counts separately.

## Comparability checks

- When the instance set, split, or ordering changes, do not compare resolve rates directly;
- when the prompt, model, budget, and review all change together, describe the result only as a system-level comparison;
- rerunning the same public task carries a risk of memorization or data contamination;
- harness incidents must be separated from patch-semantic failures;
- “old tests pass” proves only the old contract that was executed, not newly added behavior;
- stringify/hash/render results derived from the same implementation agreeing with one another do not constitute independent oracles;
- neither a 0/2 nor a 2/2 result from a tiny sample supports an estimate of overall capability.

## Boundary with formal documentation

Mechanisms proposed by an experiment and already implemented should be rewritten on architecture/reference pages using the semantics of the current code; unimplemented ideas should remain in the report and should not be presented as default capabilities. If a plan in a report later changes, preserve the original text and link from the index to the current design; do not rewrite history to make the experiment look inevitable in retrospect.

Return to the [documentation home](../index.md).

# DeepSeek V4.1 Flash on 100 SWE-bench Verified Instances

> Experiment dates: 2026-09-12 through 2026-09-14
>
> Model: DeepSeek V4.1 Flash through the `deepseek-flash` API alias, thinking disabled
>
> System: the project's enriched mini-agent harness, not vanilla mini-SWE-agent
>
> Dataset: `SWE-bench/SWE-bench_Verified`, `test` split
>
> Generation commit: `676c1290c479d77bea05407104a236721a1eb2c7`
>
> Exact instance set: `runs/verified-deepseek-v41-flash-sequential-20260912/final/instances.txt`

## Question and scope

This run asks whether the local harness can generate, evaluate, resume, audit, and
preserve a medium-sized SWE-bench run on a disk-constrained workstation. It also
provides a fixed 100-instance control set for later system comparisons.

The set was accumulated incrementally with seed-42 selection lists, ending with
`next-56-to-100-seed42.txt`. It is not the full 500-instance Verified split and
should not be treated as an independently reproduced leaderboard score. The exact
100 IDs, rather than a verbal sampling description, are the authoritative sample.

## Configuration

All instances used a 400-step and 2400-second hard generation limit, a 128K context
window, an 8192-token response cap, clean-context submission review, append-only
event logs, Bash `pipefail`, and no network inside the generation container.

Recorded API prices were $0.15/M uncached input tokens, $0.003/M cache-hit input
tokens, and $0.60/M output tokens. The first four calibration instances used a
$0.50 per-instance cost stop threshold; the remaining 96 used $0.20. These values
are stop thresholds, not prepaid budgets or claims about current provider pricing.

Runtime versions were Python 3.10.20, `swebench` 5.0.2, `datasets` 5.0.1,
`openai` 2.50.0, and Docker client/server 29.7.2 on Linux 6.8.0-136.

## Results

| View | Resolved | Unresolved | Error | Infrastructure | Ambiguous flags |
|---|---:|---:|---:|---:|---:|
| Initial official reports | 75 | 18 | 7 | 0 | 4 |
| Audited final selection | 75 | 19 | 6 | 0 | 3 |

The resolve rate is **75.0%**. Its Wilson 95% interval is **65.70%–82.45%**.
The adjusted view does not improve the score; it only reclassifies one initially
timed-out evaluation after obtaining stronger evidence.

`sphinx-doc__sphinx-7985` first hit the harness's 1800-second test timeout. The
same patch was evaluated again with a 6000-second limit and no model call. It
finished in 1981.69 seconds and was `unresolved`: three linkcheck tests failed.
The initial timeout remains in the initial view, while the completed report is an
explicit override in the audited view.

## Failure and retry audit

Six `error` instances produced malformed patches that the harness could not apply:

- `astropy__astropy-13453`
- `django__django-11163`
- `django__django-14792`
- `django__django-15957`
- `django__django-16263`
- `scikit-learn__scikit-learn-14894`

Three unresolved reports carried a `no_tests_collected` ambiguous flag:
`pylint-dev__pylint-4551`, `pytest-dev__pytest-7205`, and
`sphinx-doc__sphinx-8595`. They remain model/system failures; the ambiguous flag
is recorded separately and does not remove them from the denominator.

`django__django-15128` exhausted its first generation time limit with no usable
patch. That attempt was preserved before a replacement generation, which resolved
the instance. Its first attempt cost $0.15184442 across 209 API calls; the
replacement cost $0.08264339 across 147 calls.

Fifteen Docker image pre-pulls initially failed with EOF or short-read errors
before model generation. All 15 were later retried successfully, so they are
recovered infrastructure incidents rather than final benchmark failures. Their
exact IDs and evidence are retained in `final/annotations.json` and the archived
evaluation logs.

## Cost

| Cost view | USD | API calls |
|---|---:|---:|
| Final 100 trajectories | $3.10541145 | 7,461 |
| Preserved replaced attempt | $0.15184442 | 209 |
| Actual billed experiment total | **$3.25725587** | **7,670** |

The billed mean is $0.03257256 per instance and $0.04343008 per final resolved
instance. Evaluation retries use the local Docker harness and add no model cost.

## Artifact layout and verification

The run directory contains the mutable/raw evidence, while `final/` is a stable
summary layer. Its top-level `README.md` is the entry point, and all 100 per-task
directories are grouped under `instances/` instead of being scattered at the run
root:

- `instances/`: 100 directories, each containing one trajectory and one complete
  event log;
- `summary.json`: initial/final counts, Wilson interval, and cost accounting;
- `manifest.json`: every instance, trajectory, event log, report attempt, selected
  result, and generation retry;
- `failures.json`: all 25 final non-resolved instances and their report evidence;
- `annotations.json`: the manual retry and failure-classification audit;
- `raw-evaluation-logs.tar.zst`: 102 evaluation-log directories, including retries;
- `checksums.sha256`: hashes for every referenced raw and final artifact.

From the final directory, verify all retained files with:

```bash
sha256sum -c checksums.sha256
```

Regenerate the summary deterministically with:

```bash
conda run -n minimal-SWE-agent python scripts/finalize_swebench_run.py \
  runs/verified-deepseek-v41-flash-sequential-20260912 \
  --expected-count 100 \
  --annotations final/annotations.json \
  --report-override \
  sphinx-doc__sphinx-7985=reports/deepseek-flash.dsv41f-final-sphinx-7985-timeout6000.json
```

The finalizer rejects mismatched JSON/JSONL predictions, non-submitted statuses,
missing or count-mismatched event logs, missing classifying reports, and unknown
overrides before writing the summary.

## Interpretation

This is useful evidence for the project as an engineering portfolio: it exercises
resumable execution, low-disk image lifecycle management, retry attribution,
cache-aware cost accounting, complete trajectories, official harness integration,
and reproducible artifact verification across 100 tasks.

The 75% number is weaker evidence about general model capability. It comes from a
public, historical benchmark, covers only one incrementally selected fifth of the
split, and includes a custom review-enabled harness. Future comparisons should
keep this exact instance set and all system settings fixed, or clearly label the
result as a different system-level experiment.

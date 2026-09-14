# DeepSeek V4.1 Flash: Final Results on 100 SWE-bench Verified Instances

> Experiment dates: 2026-09-12 through 2026-09-14
>
> Model: DeepSeek V4.1 Flash (`deepseek-flash`, thinking disabled)
>
> System: this project's enriched mini-agent harness
>
> Dataset: `SWE-bench/SWE-bench_Verified`, `test` split

## Final result

| Outcome | Instances | Rate |
|---|---:|---:|
| Successful (`resolved`) | **75** | **75%** |
| Failed (not resolved) | **25** | **25%** |
| Total | **100** | **100%** |

A success means that the submitted patch applied and passed every required
FAIL_TO_PASS and PASS_TO_PASS test. SWE-bench gives no partial credit.

The 25 failures break down as follows:

| Failure cause | Count | Meaning |
|---|---:|---|
| Functional failure or regression | **17** | Patch applied, but required tests failed |
| Tests could not run correctly | **2** | One collection ImportError and one conflict with the official test patch |
| Invalid submitted diff | **6** | Truncated or malformed diff could not be applied |
| Total | **25** | No final infrastructure failures |

## Failed instances

### Required tests failed: 17

| Instance | Evidence |
|---|---|
| `astropy__astropy-14369` | 1 FAIL_TO_PASS and 2 PASS_TO_PASS failures |
| `django__django-10973` | `test_nopass` failed |
| `django__django-11239` | `test_ssl_certificate` failed |
| `django__django-11477` | 2 URL-pattern tests failed |
| `django__django-12193` | `test_get_context_does_not_mutate_attrs` failed |
| `django__django-15252` | 2 migration-schema tests failed |
| `django__django-15916` | `test_custom_callback_in_meta` failed |
| `matplotlib__matplotlib-22871` | 1 PASS_TO_PASS date-formatter regression |
| `matplotlib__matplotlib-25332` | `test_complete[png]` failed |
| `pydata__xarray-3993` | 2 `test_integrate` cases failed |
| `pydata__xarray-6938` | `test_to_index_variable_copy` failed |
| `pytest-dev__pytest-7205` | 9 FAIL_TO_PASS fixture-output cases failed |
| `sphinx-doc__sphinx-7985` | 2 FAIL_TO_PASS and 1 PASS_TO_PASS failures |
| `sympy__sympy-11618` | `test_issue_11617` failed |
| `sympy__sympy-13852` | `test_polylog_values` failed |
| `sympy__sympy-13974` | `test_tensor_product_simp` failed |
| `sympy__sympy-20916` | `test_super_sub` failed |

`sphinx-doc__sphinx-7985` initially timed out at 1800 seconds. Re-evaluating the
same patch with a 6000-second limit and no model calls finished in 1981.69 seconds
and confirmed the test failures. `pytest-dev__pytest-7205` carried an additional
ambiguous classifier flag, but its raw report clearly records nine failed tests.

### Tests could not run correctly: 2

| Instance | Cause |
|---|---|
| `pylint-dev__pylint-4551` | Collection ImportError for `get_annotation`; zero tests executed |
| `sphinx-doc__sphinx-8595` | Submitted test-file changes conflicted with the official test patch; zero tests collected |

Both failures were caused by the submitted patch, not Docker or machine failure.

### Invalid diffs: 6

| Instance | Cause |
|---|---|
| `astropy__astropy-13453` | Truncated diff; malformed at line 37 |
| `django__django-11163` | Malformed at line 40 and truncated mid-file |
| `django__django-14792` | Invalid SQLite hunk at line 121 |
| `django__django-15957` | Invalid `get_prefetcher` hunk |
| `django__django-16263` | Malformed at line 166 |
| `scikit-learn__scikit-learn-14894` | Truncated diff; malformed at line 57 |

These six failures motivate mandatory diff syntax and applicability validation
before evaluation. This run preserves them as failures; it does not repair them
after the fact.

## Cost

| Item | Cost | API calls |
|---|---:|---:|
| Final 100 trajectories | $3.10541145 | 7,461 |
| Preserved first attempt for `django__django-15128` | $0.15184442 | 209 |
| Actual experiment total | **$3.25725587** | **7,670** |

The mean cost was $0.03257256 per instance and $0.04343008 per successful
instance. Docker evaluation retries made no model calls.

## Artifacts

The complete run is in `runs/verified-deepseek-v41-flash-sequential-20260912/`:

- `instances/`: trajectories and complete event logs for all 100 instances;
- `reports/`: official evaluation reports;
- `final/summary.json`: final aggregate result;
- `final/failures.json`: all 25 failed instances;
- `final/manifest.json`: per-instance evidence and evaluation attempts;
- `final/raw-evaluation-logs.tar.zst`: archived raw evaluation logs;
- `final/checksums.sha256`: checksums for the retained artifacts.

From `final/`, verify the retained files with:

```bash
sha256sum -c checksums.sha256
```

The final result is **75 successes and 25 failures out of 100**. The failures are
17 test failures, 2 cases where tests could not run correctly, and 6 invalid diffs.

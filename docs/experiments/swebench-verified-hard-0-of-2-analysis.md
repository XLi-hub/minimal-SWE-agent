# SWE-bench Verified Hard Two-Instance Failure Retrospective: Why “All Self-Tests Green” Still Ended at 0/2

> Run directory: `runs/verified-deepseek-v4-strict-hard/`
> Model: `deepseek-v4-flash` (thinking disabled)
> Instances: `pydata__xarray-6992`, `sphinx-doc__sphinx-7590`
> Official harness: 0/2 resolved, 0 infrastructure failures

This experiment deliberately selected two previously unrun Verified instances labeled `>4 hours`. Both reached `submitted` and both passed relevant tests in the original repositories, but the official harness ultimately judged both failures. This was not an experiment in which “the model did nothing”; it was a more valuable counterexample: **the Agent performed many correct actions but did not establish sufficiently strong evidence that the patches satisfied the new behavior.**

## The result was not “submission succeeded”

| Instance | Agent API calls | Complete events | Context compression | FAIL_TO_PASS | PASS_TO_PASS | resolved |
|---|---:|---:|---:|---:|---:|---|
| `pydata__xarray-6992` | 60 | 123 | 0 | 0/12 | 945/945 | No |
| `sphinx-doc__sphinx-7590` | 124 | 249 | 1 | 0/1 | 24/24 | No |

`submitted` means only that the Agent called `submit` and produced an applicable patch. It does not mean that the new behavior is correct, and it cannot replace the official harness. Future status descriptions must distinguish:

1. `submitted`: a candidate patch exists;
2. `completed`: the harness ran successfully;
3. `resolved`: both FAIL_TO_PASS and PASS_TO_PASS requirements were met.

## xarray: the exception was fixed, but the data-model invariant was not restored

### What the Agent got right

- Reproduced the problem where `DataVariables.__len__()` returned a negative number;
- found the direct cause that `_coord_names` could contain a name absent from `_variables`;
- changed `__len__` to count actual non-coordinate variables;
- removed `drop_variables` from `coord_names` in `reset_index()`;
- ran a broad xarray test suite without breaking the 945 PASS_TO_PASS tests.

These actions were enough to stop the minimal example in the issue from raising `ValueError`, and enough to keep the old tests green. The Agent therefore concluded that the “root cause was fixed.”

### What was actually missing

The negative length was not an independent bug, but a visible symptom of inconsistent state after index reconstruction. Correct behavior also had to maintain all of the following at the same time:

- membership consistency between `_variables` and `_coord_names`;
- correspondence between `_indexes` and MultiIndex level coordinates;
- which coordinates become base variables when `set_index()` replaces an old index;
- whether `reset_index(drop=True)` actually removes dimension variables and level variables;
- whether a MultiIndex with one remaining level is renamed back to the dimension under compatibility semantics;
- whether dimensions must be recalculated through `_replace_with_new_dims()`.

The Agent fixed the consumer `DataVariables.__len__` when it read invalid state, but did not fully repair the `set_index/reset_index` lifecycle that produced the invalid state. The 12 new official tests all failed, precisely covering these state transitions.

### Mechanism lesson

“Minimal change” does not mean “change the code closest to the traceback.” When an exception reveals that an internal invariant has been broken, first ask:

> Is this state supposed to exist? If not, who produced it?

Only after confirming that an invalid state is a legal intermediate state is a defensive fix on the consumer sufficient. Otherwise it may merely hide the error.

## Sphinx: successful parsing does not mean the AST contract is complete

### What the Agent got right

- Identified that the word boundary at the start of `identifier_re` could not match a UDL suffix after a number;
- supported number, string, and character user-defined literals;
- reproduced and fixed an infinite loop during implementation;
- recovered successfully after a 120-second tool timeout without aborting the whole instance;
- passed all 25 existing `tests/test_domain_cpp.py` tests;
- parsed and stringified `6.62607015e-34q_J * 1q_s` from the issue.

### What was actually missing

Sphinx's C++ AST literal has more responsibilities than “parse and convert back to a string.” It also participates in:

- C++ symbol ID generation through `get_id(version)`;
- ABI-style name mangling for literal operators;
- signature rendering;
- identifier/xref representation of a UDL suffix as `operator""suffix`;
- distinguishing standard integer/float suffixes from UDL suffixes.

The Agent designed a validation that expected `5_udl` to have an ID resembling `L5_udlE`. The official test required the UDL to be represented as a literal-operator call, such as `clL_Zli...E...E`. Thus, although parsing and stringification were correct, the only FAIL_TO_PASS test still failed.

The Agent recognized in its trajectory that the ABI might differ, but then wrote a temporary test using its guessed ID and used that test to prove that its implementation was correct. This is a typical circular argument: **the test validated the implementer's assumption rather than the contract of the repository's existing abstraction.**

## Context compression was a secondary factor, not a universal explanation

xarray was not compressed and still failed, so the 0/2 result cannot simply be attributed to the summary losing information.

Sphinx was compressed once. The complete `.events.jsonl` retained 249 events, while the model view was reduced to a shorter history. The summary retained files, commands, and test results, but it also compressed “all relevant tests passed” and “the ID may differ from grading” into the current state. The later model mostly confirmed the existing approach instead of reopening unverified assumptions.

The compression mechanism therefore needs to retain more than “what was done”; it must distinguish:

- observed evidence;
- current hypotheses;
- rejected approaches;
- unverified contracts;
- what the tests covered and did not cover;
- risks that must still be closed before submission.

Keeping the complete trajectory and the compressed view side by side is correct. The model does not need to read the entire event journal by default; improving the evidence structure of the summary is simpler and more controllable. Only if experiments show that summaries continue to lose key evidence should pagination, limits, and a read-only history lookup tool be considered.

## What the harness should and should not change

### Should not: provide hidden-test feedback to the standard evaluation Agent

The official test patch is not visible before submission, which is a core boundary of SWE-bench. If, after the first failure, the specific FAIL_TO_PASS traceback is sent back to the model for a second fix and that second attempt is recorded as the standard score, the test answer has become a training signal and the result cannot be compared with an ordinary SWE-bench run.

A development `repair mode` can be provided, but it must:

- use a new run ID;
- mark `harness_feedback=true` in the report;
- be excluded from standard resolved-rate statistics;
- preserve every round's patch and test feedback rather than overwriting the first failure.

### Should: strengthen evidence that can be verified before submission

Without leaking hidden tests, the harness/runner can do the following:

1. **Precheck candidate patches**: require them to be non-empty and applicable, pass `git diff --check`, and avoid forbidden paths;
2. **Separate status language**: let the Runner say only candidate/submitted, while only the harness can write resolved;
3. **Audit contracts before submission**: require the Agent to recheck all public contracts of affected objects, not just the minimal reproduction;
4. **List test evidence**: record exact commands and return codes, along with the behaviors those tests cover and do not cover;
5. **Harden execution**: use offline networking, pipeline `pipefail`, process-tree timeout cleanup, and an append-only raw event log;
6. **Classify infrastructure**: keep image-pull failures, command timeouts, and test failures separate rather than merging them into unresolved.

## Basic improvements already implemented

- `5c8358e`: block common network commands before tool dispatch in strict SWE-bench, while retaining `--network=none` as a safety boundary;
- `d3c56fa`: fix duplicate/missing binding of runner compact-factory parameters;
- `ca78a76`: recheck time and cost after a summary call, and preserve usage even for a malformed summary response;
- `7fcecfe`: clean up exec process groups inside the container when the host timeout fires, and clean descendant process trees on Windows;
- `5d33c45`: make compressed summaries explicitly separate contracts, observed evidence, exact test scope, and unverified assumptions;
- `5181ce9`, `3993ac1`, `785fced`: add a general draft-submission gate, enable a no-hidden-test-feedback contract audit in SWE-bench, and make the complete trajectory distinguish draft from final submit accurately;
- continue to save the complete event sidecar, the compressed model view, and the official harness report separately.

These improvements increase runtime trustworthiness, but they do not automatically make an incorrect patch correct. The next layer must target the verification strategy directly.

## Next-layer mechanism design

### 1. Evidence-oriented summaries

Change the summary from a “progress review” into a state that supports continued reasoning: it must retain invariants/contracts, evidence, unverified assumptions, test-coverage gaps, and conditions for closing the next step. This has been implemented; whether it improves the resolved rate still requires a fixed-variable replication experiment.

### 2. Two-stage pre-submission audit

The first `submit` records only a draft. The Agent receives one explicit audit opportunity:

- Is this a root-cause fix or symptom suppression?
- Which related states are created, transformed, or deleted?
- Besides parse/execute, which ID, serialization, rendering, or lifecycle contracts of the affected objects exist?
- Does the new behavior have a local regression check independent of implementation details?
- What can passing the old tests prove, and what can it not prove?

Only the second `submit` terminates the run after the audit is complete. This mechanism cannot guarantee success, but it converts “I think it is fixed” into a set of evidence questions that must be closed. It is enabled in the SWE-bench profile; the first draft does not run hidden tests and receives no evaluator feedback.

### 3. Comparable experiment design

When rerunning the same two instances, keep the following fixed:

- the same base image, model, and thinking setting;
- the same network policy;
- the same max steps/time;
- change only one mechanism (for example, change only the summary first, then add only the submit audit);
- report resolved rate, API calls, wall time, compression count, and contract-audit behavior together.

Otherwise, even a change from 0/2 to 1/2 cannot reveal which improvement produced the effect.

## Most important lesson

This failure shows that the difficulty of complex SWE-bench instances is not “whether code can be written,” but:

> Can the Agent recover the abstraction boundaries that the repository maintainers actually protect from a limited issue description, and design evidence for verification that does not depend on its own implementation assumptions?

Green old tests, a minimal reproduction without an exception, and a small diff are all only partial evidence. A high-quality Agent must also actively look for omitted contracts and clearly acknowledge what remains unverified.

Navigation: [experiment index](index.md) · [design trade-offs](../decisions/design-tradeoffs.md) · [SWE-bench guide](../guides/swebench.md)

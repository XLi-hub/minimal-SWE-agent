# SWE-bench Hard Two-Instance Audit Rerun: Better Process, Still 0/2

> Control run: `runs/verified-deepseek-v4-strict-hard/`
> Audit rerun: `runs/verified-deepseek-v4-strict-hard-audit/`
> Model: `deepseek-v4-flash` (thinking disabled)
> Instances: `pydata__xarray-6992`, `sphinx-doc__sphinx-7590`

This rerun fixed the model, prepared image, offline policy, `max_tokens=8192`, 400 steps, 2400 seconds, 128K Agent context, and test timeout. Compared with the previous round, it introduced only evidence-oriented summaries and a same-context draft audit after the first submit. The two instances were generated sequentially and scored separately; the large Docker image was deleted immediately after scoring.

## Results

| Instance | Old API calls | New API calls | Old/new compression | FAIL_TO_PASS | PASS_TO_PASS | resolved |
|---|---:|---:|---:|---:|---:|---|
| `pydata__xarray-6992` | 60 | 41 | 0 / 0 | 0/12 | 945/945 | No |
| `sphinx-doc__sphinx-7590` | 124 | 73 | 1 / 0 | 0/1 | 24/24 | No |

Both official harness runs had zero infrastructure failures. Fewer API calls and no compression for Sphinx show improvements in process cost and trajectory length; however, there is only one sample of each, so the decrease cannot be attributed to a single mechanism, much less substituted for the resolved rate. Prices remain configured as zero, so `$0` in the trajectory means only that the project has no provider unit prices configured; it does not mean that the actual calls were free.

## What did the draft audit actually change?

### xarray

After the first draft, the Agent retraced `set_index → reset_index`, checked the MultiIndex dimension, level drops, and ordinary indexes, ran more than 2,500 related tests, and confirmed that an additional Pint failure remained after reverting the patch. These actions were more thorough than in the previous round.

But it still narrowed the issue to two local conditions:

- `DataVariables.__len__` and `__iter__` agree;
- a dropped name no longer remains in `_coord_names`.

The new official cases required the complete reset-index state machine: dimension/level handling, single/list parameters, `drop` True/False, MultiIndex degradation, conversion of `IndexVariable` to a base `Variable`, renaming, dimension recalculation, and downstream behavior under groupby. The Agent validated several examples of its own choosing, but did not first construct a behavior matrix from the public parameters and neighboring tests. All 12 FAIL_TO_PASS tests therefore still failed.

### Sphinx

After the first draft, the Agent proactively checked construction, stringify, `get_id()`, and signature, and used `git stash` to prove that the temporary regression script failed without the patch. This was no longer “only running old tests.”

The key mistake was calling the following results contract evidence:

```text
data='1q_s'  str='1q_s'  id2='L1q_sE'  signature='1q_s'
```

All of these values were derived from the same string newly inserted into `ASTNumberLiteral.data`; they proved only internal consistency. The beginning of the repository file already stated that IDs use Itanium C++ ABI mangling, and the existing `ASTOperatorLiteral` and call expression provided an independent analogy. The official expected new ID for `5_udl` was `clL_Zli4_udlEL5EE`, while the actual ID remained `L5_udlE`. The Agent saw the ID but did not first calculate the expected value from the existing literal-operator call abstraction.

## Newly discovered tool issue

Although `read(path=...)` has a 20K-character cap, it did not apply `tools.default_max_lines=100`. In the Sphinx trajectory, the same 7,000-plus-line file returned 20K characters—about 392 numbered lines—at least four times; xarray also had one 20K/511-line output. The model even pointed out the abnormal read offset in the trajectory, but could only work around it with `sed`.

This was not the sole cause of failure, but it repeatedly polluted the context and increased anchoring. `d952498` made `read` paginate by default and added `line_start`/`lines` for sequential reads, preserving original line numbers and the character limit.

## Why same-context self-review still failed

The draft audit gave the original author one more chance to inspect the work, but retained the full reasoning history. After the model has invested many steps in establishing an approach, an audit easily becomes “find more evidence that the current patch is right” rather than trying to disprove it from scratch. Both trajectories showed this confirmation bias: the validation scope expanded substantially, while the abstract choice barely changed.

The follow-up mechanism is therefore not simply a third submit, but:

1. `662459e`: let review start from a clean context composed of the system prompt, original issue, candidate patch, and audit checklist; retain the full author trajectory in append-only events rather than using it as the reviewer's default anchor.
2. `95aef11`: enable clean review in SWE-bench; require the status API to construct a behavior matrix and require identity/serialization/mangling checks to find an independent repository oracle. Multiple methods derived from the same new representation cannot testify for one another.
3. Keep standard runs free of hidden tests. If the official failure details above are used for further repair, create a separate repair run marked `harness_feedback=true` and non-comparable.

## Conclusion about parameter settings

Neither failure was caused by insufficient steps or time. The new round submitted proactively after 41 and 73 API calls respectively, far below 400 steps; neither exited because of the 2400-second limit. Raising the limit from 400 would only expand worst-case cost and would not automatically discover missing contracts. The more appropriate direction is to treat the budget as a safety ceiling and spend additional calls on independent review, comparison oracles, and behavior matrices rather than allowing the original path to continue indefinitely.

## How to interpret the next experiment

When rerunning with a clean-context review, record at least the following together:

- resolved/F2P/P2P rather than only submitted;
- whether the reviewer actually found an independent repository oracle;
- whether it constructed a behavior matrix covering the public-parameter axes before seeing the official result;
- author and reviewer API calls, tool-output character counts, and compression counts separately;
- actual provider cost (trajectory cost is trustworthy only after unit prices are configured).

Even if the next round remains 0/2, a clean reviewer that can clearly identify the ABI or state-matrix gap in the current patch is closer to an interpretable, iterative Agent design than simply adding more steps. Ultimately, effectiveness is still determined by the official resolved result.

## Later implementation: compressed memory and on-demand lookup together

The later Sphinx clean-review single-instance rerun was still `0/1`; xarray was not rerun in that round and must not be described as a second failure. Sphinx demonstrated that “clearing the author's context” alone cannot guarantee that the reviewer will find the correct ABI oracle: it removed anchoring, but also discarded the commands, tests, and file locations the author had already paid to discover. The problem should therefore not be reduced to a choice between “retain the entire history” and “clear everything.”

Three memory layers are now used:

```text
Hot context: system + original issue + bounded checkpoint + candidate + review prompt
                              │
             Reviewer actively queries when exact evidence is needed
                              ▼
Cold storage: append-only .events.jsonl (complete, read-only, not automatically fed back)
```

The checkpoint itself has two layers:

- The machine layer extracts only event sequence, tool name, command, return code, file path, and error; it does not copy the author's natural-language conclusions or the large patch from `submit`;
- The model layer compresses the working state with an evidence-focused prompt and explicitly marks it as an untrusted navigation aid; it helps the reviewer know “where it might need to look,” but cannot replace a repository oracle.

The reviewer can still use `trajectory` to read the original records, but reading is now active, local, and composable. In addition to `query/start/events`, it can filter by `event_type`, `role`, `tool_name`, and `returncode`. For example, it can first find `tool_name=bash, returncode=1`, then use the event sequence to look up the surrounding context without restoring the author's full history.

The corresponding implementation was split into three commits to avoid coupling storage, handoff, and lookup in one large change:

| commit | purpose | what it does not claim to solve |
|---|---|---|
| `7cd2cf7` | Build a deterministic, bounded evidence checkpoint with correlated calls/results from raw events | Does not judge patch correctness |
| `b5ec908` | Force checkpoint generation at the clean-review boundary and retain an untrusted compressed summary | Does not guarantee that the reviewer will find the right oracle |
| `b5691c5` | Add structured combined filtering to `trajectory` | Does not automatically choose which evidence to inspect |

This mechanism targets the **information-organization problem** in the two failures, not the tasks themselves:

| Benchmark instance | Official result | Fundamental failure | What the new mechanism may improve | What it cannot replace |
|---|---:|---|---|---|
| `pydata__xarray-6992` | FAIL_TO_PASS 0/12, failure | No complete behavior matrix was constructed from public parameters and state transitions | The checkpoint preserves patterns already run, reducing repeated exploration by the reviewer; `trajectory` can find existing failure/success comparisons | The reviewer must still actively enumerate dimension, level, drop, MultiIndex, and other axes |
| `sphinx-doc__sphinx-7590` | FAIL_TO_PASS 0/1, failure | Modeled a UDL as an ordinary number rather than a literal-operator call, causing an ABI ID error | The checkpoint exposes the scope of parse/stringify/ID checks; the reviewer can look up exact commands and return codes | The reviewer must still find `ASTOperatorLiteral`/call expression as an independent oracle and assert the exact ID |

It is therefore incorrect to say that these three changes solved the previous two benchmark failures. The more accurate hypothesis is that they reduce information loss caused by a clean reset without restoring the anchoring of a complete reasoning history; whether they improve the resolved rate must be tested by a rerun under the same configuration.

## Next controlled experiment (not yet executed)

The next round should keep the model, prepared image, offline policy, 400 steps, 2400 seconds, 128K context, and test timeout unchanged, using only the memory handoff as the experimental variable. The report must separate the instances instead of summarizing them as one generic “bench run”:

1. Run `sphinx-doc__sphinx-7590` first. The success threshold is FAIL_TO_PASS 1/1 and PASS_TO_PASS 24/24; also record whether the reviewer established an exact expression-ID oracle before submission.
2. Then run `pydata__xarray-6992`. The success threshold is FAIL_TO_PASS 12/12 and PASS_TO_PASS 945/945; also record whether the reviewer formed a complete behavior matrix before seeing official feedback.
3. Report each instance's author main-loop calls, checkpoint-summary calls, reviewer calls, `trajectory` calls and filters, compression count, wall time, and actual cost separately.
4. If it still fails, first determine whether the checkpoint lost evidence, the reviewer did not actively retrieve it, or the reviewer still chose the wrong abstraction after retrieval. These failure types require different fixes and must not all be blamed on insufficient steps.

Do not also change the model, large sections of the prompt, and hard budget in this round; otherwise, even success would not reveal which change mattered. Continue using 400 steps as a runaway-protection ceiling rather than a target; checkpoint-summary requests count toward calls, cost, and wall time, but not toward the main-loop step count.

Navigation: [experiment index](index.md) · [Agent loop](../architecture/agent-loop.md) · [tool reference](../reference/tools.md)

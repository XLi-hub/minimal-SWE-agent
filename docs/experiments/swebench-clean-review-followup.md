# SWE-bench Verified Single-Instance Report: Sphinx 7590 (Failure)

## 1. Which benchmark instance was run

| Item | Value |
|---|---|
| benchmark | SWE-bench Verified |
| split | `test` |
| instance ID | `sphinx-doc__sphinx-7590` |
| repository under test | `sphinx-doc/sphinx` |
| model | `deepseek-v4-flash` (thinking disabled) |
| experiment mechanism | Reset the context after the author's first submission and have an independent clean reviewer recheck it |
| runtime limits | Strictly offline container, 400 steps, 2400 seconds, 128K context, 8192 output tokens |
| run directory | `runs/verified-deepseek-v4-strict-hard-clean-review/` |

**This round ran only `sphinx-doc__sphinx-7590`. The xarray instance from the original plan was not run and must not be recorded as either a success or a failure.**

## 2. Official result: failure

| instance ID | resolved | FAIL_TO_PASS | PASS_TO_PASS | infrastructure failure |
|---|---:|---:|---:|---:|
| `sphinx-doc__sphinx-7590` | **0/1 (failure)** | **0/1** | 24/24 | 0 |

The Agent ultimately submitted a patch, but “submitted a patch” does not mean that the instance was solved. The official SWE-bench harness's newly failing test did not pass, so the final state of this instance is **unresolved**.

The process cost was:

| Stage | model queries | tool calls | context compression |
|---|---:|---:|---:|
| author | 90 | 90 | 0 |
| clean reviewer | 24 | 24 | 0 |
| total | 114 | 114 | 0 |

## 3. What this instance required

The instance required Sphinx to correctly understand C++ user-defined literals (UDLs). For example, `5_udl` looks like a number with a suffix, but in C++ it represents a call:

```cpp
operator ""_udl(5)
```

The expression above explains the semantics; it is not an equivalent rewrite of the original source. Sphinx needed to parse `5_udl` as “call the literal operator `_udl` with argument `5`” and generate a stable internal ID for this C++ expression.

The task therefore was not merely to make the parser accept the text. It also had to:

1. read `5_udl`;
2. render it again as `5_udl`;
3. represent its internal semantics as a call to the `_udl` operator, rather than treating the entire `5_udl` as an ordinary number.

## 4. What the Agent did, and why it still failed

**In one sentence: the Agent fixed “reading this text” but did not fix “understanding this text.”**

The candidate patch directly appended `_udl` to the number content and stored `5_udl` as a whole in an ordinary number node. This produced the following results:

| Check layer | Correct behavior | Candidate patch | Result |
|---|---|---|---|
| Read source | Accept `5_udl` | Accepted it | Passed |
| Render again | Output `5_udl` | Could output it unchanged | Passed |
| Understand semantics | The `_udl` operator receives argument `5` | Treated `5_udl` as an ordinary number | **Wrong** |
| Generate internal ID | Generate an ID for an operator call | Generated an ID for a numeric literal | **Wrong** |

This is like copying an instruction verbatim without recognizing that it is a function call. The surface text is correct, but the internal representation remains wrong.

The official new test checked exactly this internal semantics:

| Input | Official expected expression ID | Candidate actual result |
|---|---|---|
| `5_udl` | `clL_Zli4_udlEL5EE` (operator call) | `L5_udlE` (ordinary number) |

The two IDs themselves are not important. They simply demonstrate that the official requirement is a call to the `_udl` operator, while the candidate submission produced a number named `5_udl`. Consequently, the new test failed, the final `FAIL_TO_PASS = 0/1`, and the entire instance was judged a failure.

The clean reviewer performed the following checks:

- Compared parsing with and without the patch and confirmed that multiple UDL inputs changed from parse failures to parseable inputs;
- covered decimal floats, integers, hexadecimals, binaries, and ordinary literals;
- ran `tests/test_domain_cpp.py`, with all 25 existing tests passing;
- checked declaration stringify, signatures, and several IDs.

The reviewer missed this issue because it mainly checked the first two layers—whether the text could be read and rendered unchanged—without directly checking whether it represented an operator call semantically. Specifically:

1. The 25 existing tests did not assert an exact expression ID for the new UDL inputs, so they could not serve as an oracle for this behavior;
2. The member-declaration IDs checked by the reviewer do not encode the initializer and were unrelated to the failure point;
3. `parse`, `stringify`, and `get_id` all derived from the same incorrect `ASTNumberLiteral` representation, so their consistency was not independent validation.

Thus, the core failure was: **the implementation treated a UDL as a special number, while C++ requires it to be represented as an operator call; the reviewer also did not establish a direct test for that call semantics.**

## 5. Did a Docker/harness incident cause the failure?

An independent harness problem did occur during the run: while `edit` modified the roughly 7,288-line `cpp.py` twice, the file was truncated to 0 bytes. The model then restored the file with `git checkout` and used a script for the replacement.

This incident increased the number of calls and the runtime, but **did not cause the official semantic failure**, for the following reasons:

- the final candidate patch was applied successfully;
- the official harness reported `infrastructure failure = 0`;
- all 24/24 PASS_TO_PASS tests passed;
- the only FAIL_TO_PASS test consistently reported that the expression ID did not match the expected value.

The root cause was that the old Docker timeout wrapper put the `setsid` command in the background, after which a non-interactive shell connected stdin to `/dev/null`; `cat > file` inside `docker exec -i` truncated the file first and then immediately read EOF. This issue was fixed in `972f025`, which was validated with a real Docker round trip of a roughly 300 KiB file and timeout descendant cleanup.

The two conclusions should therefore be recorded separately:

- benchmark failure: incorrect UDL identity/mangling implementation;
- harness runtime incident: lost stdin caused large-file truncation, but the file was restored and the issue was fixed; it does not change this benchmark's failure classification.

## 6. Conclusion about the clean-review mechanism

The clean reset itself worked as designed: the reviewer's initial context contained only the system prompt, the original issue, the candidate patch, and the audit requirements; it did not inherit the author's 90-turn conversation. This reduced the risk of directly reusing the author's conclusions, but it did not automatically produce a genuinely independent oracle.

This result supports only the following conclusions:

- Separating the default author and reviewer contexts is valuable;
- resetting the context alone does not guarantee review quality;
- a reviewer must enumerate every observable behavior of a new construct and directly assert exact results;
- old tests can serve as an oracle for new behavior only when they include the new inputs and assert the relevant outputs;
- multiple results derived from the same incorrect AST cannot be presented as independent evidence.

## 7. Changes already completed

The following changes have already been implemented and committed; they are not future proposals:

| commit | status | purpose |
|---|---|---|
| `fc73234` | completed | Warn when provider prices are all zero and a cost cap is therefore not actually enforceable |
| `d05e11a` | completed | Reliably clean up child processes after a Docker timeout |
| `972f025` | completed | Preserve stdin for Docker file-write commands so large files are not truncated |
| `6477834` | completed | Provide a read-only, searchable, paginated `trajectory` evidence tool |
| `e98d73c` | completed | Enable `trajectory` in the SWE-bench clean reviewer and tighten exact-oracle rules |
| `7cd2cf7` | completed | Establish a machine-evidence checkpoint without author conclusions or the candidate body |
| `b5ec908` | completed | Give clean review both the machine index and untrusted compressed working memory; degrade gracefully if summarization fails |
| `b5691c5` | completed | Support combined filtering by event, role, tool, and return code in `trajectory` |

The trajectory continues to use two storage layers: `.traj.json` stores a compact model-facing view, while `.events.jsonl` stores the complete append-only journal. By default, the clean reviewer does not inherit the author's full conversation. Instead, it receives a bounded machine-evidence index and a model summary explicitly marked untrusted; when needed, it can use `trajectory(query, start, events, event_type, role, tool_name, returncode)` to retrieve original commands and output. All filters are combined with AND. What is retrieved is evidence to verify, not an automatically trusted conclusion.

## 8. Next steps (not yet executed)

1. First configure real DeepSeek token prices or explicitly define a provider cost policy so that `agent.cost_limit` actually takes effect; the checkpoint may make at most one additional summarization request, and that call must also be included in the real cost.
2. Rerun only `sphinx-doc__sphinx-7590`, keeping the offline policy, step count, time limit, and context limit unchanged, with the new memory handoff as the sole primary variable.
3. Require the reviewer to establish a direct test for the exact expression ID of UDLs under the relevant identity version; checking only parse/stringify or rerunning old tests does not count as a successful review.
4. Use the official `FAIL_TO_PASS = 1/1` and `PASS_TO_PASS = 24/24` as the success threshold, while recording whether the reviewer called `trajectory`, which structured filters it used, what evidence it retrieved, and whether repeated exploration decreased.
5. Run xarray only after Sphinx produces an interpretable result. If the reviewer still treats insufficiently related evidence as an oracle, consider a truly separated critic/author protocol or a different reviewer model instead of continuing to stack prompts.

Xarray was not run in this round because Sphinx directly falsified the assumption that “clean reset + the original audit prompt is sufficient” after 114 calls, while a price of zero meant that the cost limit could not constrain real spending. This was a clearly recorded early stop, not a missing or hidden xarray result.

## 9. Result and evidence locations

- Run directory: `runs/verified-deepseek-v4-strict-hard-clean-review/`
- Official scoring report: `runs/verified-deepseek-v4-strict-hard-clean-review/reports/deepseek-v4-flash.deepseek-v4-strict-hard-clean-review-sphinx.json`
- Official test output: `logs/run_evaluation/deepseek-v4-strict-hard-clean-review-sphinx/deepseek-v4-flash/sphinx-doc__sphinx-7590/test_output.txt`

**One-sentence conclusion: this round ran only SWE-bench Verified's `sphinx-doc__sphinx-7590`, and the official result was a failure; the failure came from semantic modeling of the UDL expression ID, not the Docker incident, and the next round will first rerun the same instance using the strengthened trajectory access and exact-oracle rules.**

Navigation: [experiment index](index.md) · [SWE-bench guide](../guides/swebench.md) · [trajectory format](../reference/trajectory-format.md)

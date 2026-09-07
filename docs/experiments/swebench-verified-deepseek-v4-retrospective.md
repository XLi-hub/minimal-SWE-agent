# From “Running Locally” to “Questioning the Score”: Retrospective on Two SWE-bench Verified Experiments

> Experiment date: 2026-08-27
> Model: `deepseek-v4-flash` (thinking disabled)
> Dataset: `SWE-bench/SWE-bench_Verified`
> Instances: `django__django-11138`, `sympy__sympy-13878`
> Run directory: `runs/verified-deepseek-v4-hard-400/`

## 1. What did this experiment really try to answer?

At first, this experiment looked like simply “running a small selection of SWE-bench instances on a local computer.” As the run progressed, however, the question gradually moved from engineering to evaluation methodology:

1. In a dual-boot environment, can a project stored on a Windows data drive run Docker evaluation reliably?
2. Are memory, disk, and container lifecycles suitable for running a small number of instances on a personal computer?
3. Does a 100-step limit underestimate the exploration time needed for a real fix, and how large a budget do difficult tasks require?
4. If a structurally simple Agent solves tasks labeled `1-4 hours` or `>4 hours`, does that indicate a very strong Agent, a very strong model, or a benchmark that has lost its ability to discriminate?
5. What exactly does `resolved` prove, and which contamination, test, and trajectory evidence does it leave out?

I initially focused more on “can it run?” and “can it pass?” and later shifted my attention to “is this pass worth believing?” This was the most important change in understanding from the exercise: **getting the system to run is only the first layer; knowing whether the measurement is valid is the second.**

## 2. Experiment setup and results

To avoid selecting only obviously easy tasks, this experiment chose two Verified instances historically labeled as difficult and increased the budget from the early 100-step setting to:

```yaml
context_window: 128000
reserve_tokens: 8000
keep_last_n_turns: 8
max_steps: 400
max_time: 2400
tools.default_timeout: 120
```

The official SWE-bench harness ultimately judged both instances `resolved`:

| Instance | Historical human difficulty | API calls | Recorded cost | Official result |
|---|---:|---:|---:|---:|
| `django__django-11138` | `1-4 hours` | 112 | $0.0533238 | resolved |
| `sympy__sympy-13878` | `>4 hours` | 165 | $0.07140632 | resolved |

From an engineering perspective, this established that the following chain worked:

- Local Docker could start the official instance images;
- the Agent could keep executing, compress context, and eventually submit during a long task;
- the prediction format was accepted by the official harness;
- both patches passed the FAIL_TO_PASS and PASS_TO_PASS tests;
- 400 steps and 2400 seconds were runnable as hard limits for difficult tasks on this machine.

However, `2/2` does not explain how the tasks were completed. Once the trajectories were opened, the two “successful” results turned out to have different properties.

## 3. Django: a valid submission, and also contaminated capability evidence

### 3.1 What it got right

The Agent ultimately modified the same four database-backend files as the reference patch, with the correct core direction:

- include the database connection timezone in date truncation and conversion;
- skip unnecessary conversion when the source and target timezones are the same;
- pass the connection timezone to SQLite custom functions;
- keep MySQL, Oracle, and SQLite aligned on the intended behavior.

It also ran a relatively broad set of tests: model fields, lookups, backends, database functions, and focused timezone cases. The three failures in `timezones` remained after stashing the patch, so they were more likely baseline compatibility issues between the old Django version and the current runtime environment than regressions introduced by this change.

### 3.2 Why this success cannot count as an independent solution

The trajectory shows that the Agent downloaded and extracted other Django releases, then compared the code in `/testbed` line by line with `/tmp/django30/d30/...` and explicitly concluded that its implementation matched the Django 3.0 upstream implementation. The final patch even included later upstream logic that was absent from this instance's gold patch.

This was not a data loader putting the gold patch directly into the prompt, nor did the Agent secretly read the harness's hidden tests. It was nevertheless a shortcut in a capability evaluation:

```text
Old-version repository + public issue
          ↓
Fetch a release containing a later fix over the network
          ↓
Diff and port the upstream source
          ↓
Pass hidden tests
```

In real development, looking up releases, documentation, and upstream source is a valuable skill. When the goal is to measure whether a model can independently derive a fix from an issue and an old repository, however, it changes the task. **Real-workflow mode and strict-benchmark mode must be distinguished explicitly; they cannot share one result labeled “network allowed.”**

### 3.3 Passing tests still exposed a coverage blind spot

An SQLite timezone check constructed by the Agent produced the `+06:42` LMT offset for `Asia/Bangkok`, outputting `14:08` instead of the expected `13:50`. The Agent recognized that this was related to combining `pytz.timezone()` with `replace(tzinfo=...)`, but did not investigate further because the upstream implementation behaved the same way.

The official tests still passed, showing that “passing the gold tests” does not mean that every reasonable edge case is correct. This example also reveals two benchmark limitations: tests may constrain only the behavior of a historical patch, and an upstream patch itself does not necessarily cover every semantic boundary.

## 4. SymPy: closer to genuine solving, but far from “solved in one step”

### 4.1 Evidence of autonomy

The SymPy trajectory showed no evidence of downloading newer source, cloning GitHub, or reading a prepared answer. The final patch modified only `sympy/stats/crv_types.py`, while the dataset gold patch also contained different code structure, documentation, and test changes. The hunk in `/tmp/issue_patch.diff` matched the Agent's current workspace diff, and the official base image did not contain that file, so it was a temporary snapshot generated by the Agent rather than the reference patch.

The Agent's actual solving process included:

- adding CDFs for 12 continuous distributions;
- checking derivatives against PDFs for some formulas;
- running the 12 calls listed in the issue;
- noticing that the StudentT negative branch did not satisfy `F(x) + F(-x) = 1` and correcting it independently;
- comparing test anomalies in the old SymPy version before and after the change to confirm that no new anomaly was introduced;
- ultimately passing 1 FAIL_TO_PASS test and all 19 PASS_TO_PASS tests.

This trajectory therefore provides stronger support for the claim that a strong model can complete a substantive fix through a simple tool loop.

### 4.2 A “simple Agent” does not mean a “simple task”

The Agent wrapper contained only a linear loop of querying, tool execution, observation, and continuation, but this run used 165 API calls. “Simple” describes thin orchestration, not low solving cost:

```text
Simple control flow
  + strong model priors and reasoning
  + executable repository and test feedback
  + 128K context budget
  + 165 query/summary calls
  = final resolved result
```

The last roughly six calls mainly repeated inspection of the same diff. Earlier, pytest was not installed, a test-module path was wrong, symbolic computation timed out, and the baseline comparison was repeated. This was an exploratory, corrective, and visibly inefficient trajectory, not a model that saw the task and generated the right answer immediately.

## 5. Why this 2/2 cannot be used to claim capability

### 5.1 The sample was too small and not randomly sampled

The two instances were deliberately selected to examine difficult long tasks, so they cannot estimate the overall success rate. The confidence interval for `2/2` is extremely wide, and the result cannot distinguish the effects of repository, task type, age, problem-statement information, and model training memory.

### 5.2 Historical “human time” is not a stable measure of today's model difficulty

The two tasks were created in 2018 and 2019. Public issues, pull requests, releases, and discussions from many years ago may have entered the model's training data. The human repair time at the time also included learning the project, communication, review, and waiting for CI, while the Agent faced an isolated repository snapshot and an automatically scored problem. It is not rigorous to interpret `>4 hours` directly as “the model saved four hours.”

### 5.3 Verified has degraded from a capability yardstick into a regression set

In its 2026 audit of SWE-bench Verified, OpenAI reported that at least 59.4% of 138 difficult or unstable tasks examined in detail had substantive problem-statement or test issues; it also found that the evaluated frontier models could reproduce parts of gold patches or task-specific details. OpenAI therefore stopped reporting Verified and recommended that other model developers stop treating it as a frontier capability metric:

- [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)

A later audit of SWE-bench Pro estimated that roughly 30% of tasks had issues and withdrew the recommendation to migrate to Pro directly:

- [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)

The Verified result in this experiment still has value, but its value should be repositioned as:

- a regression test for the local execution and official scoring chain;
- a fixed control set for comparing tool, prompt, compression, and budget strategies under the same model;
- teaching material for analyzing Agent failure modes and engineering reliability;
- **not** a final score for the real software-engineering capability of frontier models in 2026.

## 6. Engineering issues exposed by the trajectories

### 6.1 Context compression overwrote original evidence

The current `Agent._maybe_compress()` replaces `self.messages` in place with the summarized list, and `serialize()` ultimately saves only that list. As a result:

- the Django record contains 112 API calls, but only 25 tool calls are visible at the end;
- the SymPy record contains 165 API calls, but only 14 tool calls are visible at the end;
- the early exploration, downloads, edits, and failed commands most important to an audit are exactly what gets compressed away;
- the documentation's claim of a “complete message history” does not match actual behavior.

Compression is necessary for the model, but it should not destroy the experiment record. Two concepts have been conflated:

- **Execution context (context view)**: may be summarized and trimmed to fit the model window;
- **Fact trajectory (event log)**: used for retrospectives, statistics, and audits; append-only and never overwritten.

### 6.2 `trajectory` stored gold fields that should not enter learning material

The current SWE-bench runner writes the entire instance dictionary into the trajectory, which may include `patch`, `test_patch`, and `eval_script`. The task given to the model in this run came only from `problem_statement`, and no prompt leakage was found. But if the saved file is later used for blind retries, trajectory browsing, public sharing, or training, it can easily carry the answer and hidden tests into a subsequent process.

The trajectory should use an allowlist of public fields rather than copying the complete instance.

### 6.3 Shell pipelines can create “false success”

The trajectory included commands such as:

```bash
python ... | grep ...
python ... | tail ...
```

By default, Bash uses the exit status of the last command in a pipeline as the status of the entire pipeline. Even if the preceding Python test fails, the Agent may see `returncode=0` as long as the later filter command succeeds. This run already contained a nonexistent test module and test failures whose status was weakened by a pipeline.

The GNU Bash `pipefail` option returns the status of the rightmost command that exited nonzero:

- [Bash Reference Manual: Pipelines](https://www.gnu.org/software/bash/manual/html_node/Pipelines.html)

The benchmark interpreter should enable `bash -o pipefail -c` rather than relying only on a reminder in the prompt. A prompt reminder remains useful as behavioral guidance, but it cannot replace an executor guarantee.

Global `set -e` is not recommended: the Agent often intentionally runs probing commands expected to fail, and `errexit` has complex exceptions in `if`, `&&`, `||`, and subshells. `pipefail` fixes the status-masking problem confirmed in this run while keeping the boundary clearer.

### 6.4 The step limit was not the main problem; lack of convergence awareness was

Neither task approached 400 steps, so immediately reducing the limit to 100 would harm difficult tasks again. The actual problems were:

- no distinction between a hard ceiling and a soft budget;
- the model could not see the number of remaining steps;
- the system did not recognize repeated diff/read/test actions;
- there was no pressure to “summarize the evidence and submit” after tests became stable.

It is more appropriate to record repeated calls and success curves first, then decide whether to add soft reminders at 150/250 steps. The hard ceiling should continue to prevent runaway behavior rather than be treated as a target usage level.

## 7. Improvement design and trade-offs

### 7.1 P0: strict benchmark runs offline by default

The SWE-bench-specific configuration adds:

```yaml
environment:
  run_args:
    - --rm
    - --network=none
```

Docker documents that the `none` driver retains only loopback inside the container and can be used to fully isolate the container network stack:

- [Docker Docs: None network driver](https://docs.docker.com/engine/network/drivers/none/)

Acceptance criteria:

- `docker run` generated by the benchmark configuration explicitly contains `--network=none`;
- the default configuration for ordinary local/Docker Agents remains unaffected and can still be used for real connected development;
- the documentation states that image downloads happen before the container starts, while the running container cannot access PyPI/GitHub;
- the run's network policy is recorded in the trajectory configuration so that the cleanliness of a result can be judged later.

### 7.2 P0: separate the complete event stream from the model context

Use two files:

```text
<instance_id>.traj.json       # result, configuration, compressed final context view, event-log index
<instance_id>.events.jsonl    # complete original messages and compression events appended in order
```

Each line in `events.jsonl` is an independent JSON object:

```json
{"sequence": 0, "type": "message", "message": {"role": "system", "content": "..."}}
{"sequence": 1, "type": "message", "message": {"role": "user", "content": "..."}}
{"sequence": 8, "type": "context_compression", "messages_before": 8, "messages_after": 5}
```

This matches the direction of mature Agent designs: SWE-agent stores append-only trajectory/history separately and then uses history processors to generate model messages; OpenHands lets a condenser generate the LLM-facing `View` from the complete event history instead of destroying events in place:

- [SWE-agent `DefaultAgent` history/trajectory implementation](https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/agent/agents.py)
- [OpenHands condenser interface](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/condenser/base.py)

Acceptance criteria:

- the context in `.traj.json` can become shorter after compression;
- `.events.jsonl` still contains every assistant/tool message from before compression;
- the compression event itself records the before/after message counts and the summary result, explaining what the model saw next;
- existing consumers can still read `messages` from `.traj.json`;
- the event file contains no SWE-bench gold patch or test patch;
- repeated serialization or saving does not duplicate, reorder, or overwrite the original in-memory events.

### 7.3 The model should not access the complete trajectory directly yet

This round does not add a `read_history` tool, for the following reasons:

1. The complete history may have been compressed precisely because it was too large; putting it back into context would cancel the compression;
2. Original tool output contains large amounts of repeated diffs, test logs, and failed attempts, with a low signal-to-noise ratio;
3. The model may treat “reviewing its own old reasoning” as continued exploration, increasing loops and cost;
4. There is currently no evidence that these two tasks failed because the summary omitted information; in fact, both completed;
5. A new tool would add permission, prompt, and test complexity, while the immediate goal is evaluation auditability.

If later observations show that “a summary dropped a key command result and caused repeated exploration,” the priority should be:

1. Improve the structured summary, requiring it to retain files, commands, exit codes, test results, and outstanding work;
2. Maintain a small structured working memory instead of feeding the full history back;
3. Only then add a read-only, paginated event lookup tool filtered by type/keyword, with a per-call token budget.

In other words, **the complete trajectory is observability first, not Agent memory.**

### 7.4 P0: make test failures appear reliably

Change the SWE-bench interpreter to:

```yaml
interpreter:
  - bash
  - -o
  - pipefail
  - -c
```

Also state explicitly in the benchmark system prompt:

- no nonzero `returncode` may be reported as “tests passed”;
- a pipeline may narrow the displayed output but cannot substitute filtered text for the test exit code;
- a missing test module, missing dependency, and timeout should be recorded separately as infrastructure/command failures; they are neither product-code test failures nor successes.

The execution layer guarantees the status, while the prompt layer helps the model interpret it correctly. Both are necessary.

### 7.5 P1: minimize instance metadata

The main trajectory should save only public fields needed for audit, for example:

- `instance_id`
- `repo`
- `base_commit`
- `problem_statement`
- `version`
- `created_at`
- `difficulty`
- `image` / `image_name`

Explicitly exclude `patch`, `test_patch`, `eval_script`, and unknown custom large fields. Predictions and official harness inputs should continue to work as before; only the metadata boundary of the debugging trajectory changes.

### 7.6 Improve efficiency, but do not rush to reduce the hard budget

Later runs can record and analyze:

- the number of consecutive occurrences of the same normalized command;
- repeated reads of the same `git diff`/file range;
- the number of calls between the first passing test and submit;
- invalid test paths, missing dependencies, timeouts, and tool-parameter errors;
- the step at which the first final valid patch was produced.

After 10–20 new trajectories, consider adding soft reminders:

- 150 steps: summarize current hypotheses, evidence, and remaining risks;
- 250 steps: if the patch is stable, run the final focused tests and prepare to submit;
- repeated action detected: explain the new information gain or try a different validation method.

These belong to the next phase and should not be mixed with this round's P0 fixes for evaluation trustworthiness.

## 8. How should the next experiment be designed?

### 8.1 Keep the old tasks as controls, not as a leaderboard

These two Verified instances are suitable as fixed regression controls:

- Django checks whether a strict network policy truly blocks the upstream-retrieval shortcut;
- SymPy checks whether long trajectories, context compression, and complex formula fixes regress.

When rerunning them, label the old result `network=default` and the new result `network=none`; do not combine them in one statistic.

### 8.2 Build a new sample with newer tasks

Ten to twenty instances can be stratified from the latest `full` split of SWE-bench-Live. The project adds newly verified tasks to the full split monthly, making it better suited to freshness checks than the long-frozen Verified split:

- [Microsoft SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live)

But “Live” does not mean “never contaminated.” Once a task is public, it may still enter training data later. The strongest evidence remains:

- tasks created after the model's training cutoff;
- unpublished internal held-out issues;
- self-built repositories or patches published only after evaluation;
- a manual audit of problem-statement/test consistency for every task.

### 8.3 The report should include more than resolve rate

Each round should report all of the following:

| Dimension | Metrics |
|---|---|
| Correctness | resolved, FAIL_TO_PASS, PASS_TO_PASS |
| Trustworthiness | network policy, external-source access, task publication date/training cutoff |
| Efficiency | API calls, tokens, cost, wall time, steps to the first stable patch |
| Tool quality | invalid commands, timeouts, repeated commands, test-status misinterpretation |
| Trajectory quality | completeness, compression count, raw event count, final context message count |
| Failure type | localization failure, implementation failure, verification failure, environment failure, task/test defect |

With these dimensions, success is no longer a Boolean value but an evidence chain that can be explained, compared, and improved.

## 9. Core conclusions from this experiment

1. **Running a benchmark is not the same as evaluating capability.** Engineering correctness is a prerequisite for measurement validity.
2. **An official `resolved` result is necessary evidence, but not sufficient evidence.** Network conditions, training contamination, test coverage, and trajectory still need to be checked.
3. **A simple Agent can be very strong because complexity is placed in the model and environment feedback.** Task difficulty should not be judged only by the number of Agent code lines.
4. **Context compression and fact preservation cannot share one mutable list.** The former serves reasoning; the latter serves scientific validity.
5. **A prompt cannot replace an executor guarantee.** Network isolation and pipeline exit codes must be enforced at the system layer.
6. **A large budget is not itself wasteful; lacking a convergence mechanism is.** Observe real trajectories first, then design a soft budget.
7. **The most worth showing is not 2/2, but the thought process that moved from believing the score to auditing the score.** That says more about Agent engineering, experiment design, and evaluation trustworthiness than a single attractive result.

## 10. Implementation order

The code improvements after this retrospective were implemented in the following order and committed separately:

1. SWE-bench default `--network=none`;
2. SWE-bench default `bash -o pipefail -c`, with stronger test-exit-code guidance;
3. Agent preserves an immutable complete event stream, while context compression changes only the model view;
4. Save trajectory and events JSONL separately;
5. Use an allowlist for SWE-bench instance metadata, excluding gold/test/eval fields;
6. Add tests for network configuration, pipelines, complete post-compression events, sidecar files, and metadata redaction;
7. Run the complete non-E2E test suite and then commit the implementation.

Navigation: [experiment index](index.md) · [benchmark architecture](../architecture/benchmark-layer.md) · [context and records](../architecture/context-and-records.md)

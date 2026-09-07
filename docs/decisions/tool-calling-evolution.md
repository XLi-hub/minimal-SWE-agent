# Tool-Calling Evolution

This page records why the tool protocol evolved from text parsing to function calling, explicit
submit, dedicated file tools, a registry, and review lookups. It explains the history and does not
replace the current [Tool Reference](../reference/tools.md).

## v1: Regex Parsing of Text Blocks (Removed)

Early models emitted natural language and a specific fenced code block, and Agent used a regex to
extract shell commands. The advantage was compatibility with models that only emitted text; the
disadvantage was that label spelling, fences, multiple commands, and explanatory text made the
parser fragile.

A parse failure also required injecting a corrective message. That wasted steps and conflated “the
model did not follow the template” with “command execution failed.” Adding a tool meant adding more
text syntax, eventually producing an incomplete custom protocol.

## v2: OpenAI Function Calling

The model switched to returning `tool_calls`, with an ID, function name, and serialized arguments for
each item. Agent no longer had to guess commands from natural language, and schemas could describe
parameters to the model.

Structured does not mean trustworthy. `arguments` may still be malformed JSON, an array, or a scalar;
the name may be unknown, and field types may be wrong. The dispatcher must therefore validate at
runtime and append an error observation for the original call ID.

This step established the first core invariant: every assistant tool call must have a corresponding
tool response. Even an invalid call must not break the trajectory.

## v2.1: Bounded Output and Timeout

bash gained `lines` and `timeout`, followed by a character budget to prevent long single lines such
as minified JSON or base64 from bypassing the line limit. Truncation keeps the head and tail and
offers guidance for continuing to read.

Environment command results evolved from bare strings into `ExecutionResult`, distinguishing output,
return code, and executor exceptions. Local timeout terminates the entire process group; the
SWE-bench shell adds `pipefail` so a zero status from the last pipeline item cannot hide an earlier
test failure.

The project did not establish an asynchronous background-task system. For long commands, explicitly
increase the timeout for that call; this trades a smaller control surface for a predictable linear
event history.

## v3: Explicit submit

“The model did not call a tool” cannot distinguish completion, getting stuck, and truncation.
`submit(output)` turns completion into an explicit protocol event, allowing `Agent.run()` to return a
structured `exit_status` and submission.

The loop also introduced step, wall-clock, and cost exits. It must check time and cost again after a
query, so that a provider request that already crossed a limit cannot be followed by a file write or
shell command. Calls after submit in the same batch are acknowledged but skipped, preserving the
protocol without side effects.

## v4: read/edit/write

Using bash for everything was small but had two problems: shell quoting made file writes fragile, and
tools such as `sed` could fail to match while reporting a misleading state. Dedicated file tools put
these operations behind the Environment interface:

- read provides line numbers and pagination;
- edit requires a unique match for the old string;
- write explicitly replaces the entire file.

A bash-only profile remains available as a teaching comparison, but dedicated file tools are enabled
by default. They align local/Docker file semantics and let event evidence identify file paths directly.

## v4.1: Schema/Handler Registry and Tooling Split

As the number of tools grew, a scattered schema list and name-based if/elif branches could drift.
`TOOL_REGISTRY` now combines each name, schema, and handler into a `ToolDefinition`; the configured
list controls both model visibility and execution authorization.

Reusable details then moved into `tooling/`: schemas, types, files, output, and network. `tools.py`
retains handlers and runtime dispatch, changing the design from “all logic in one file” to a stable
facade plus small modules.

## v5: Draft Review and trajectory

The first submit can be captured as a draft and then reviewed by the model before final submission.
To reduce author-reasoning anchoring, the SWE-bench profile can reset messages; clearing everything,
however, would waste prior exploration.

The current compromise has three layers:

1. append-only events retain the original record;
2. the evidence checkpoint extracts machine facts only;
3. `trajectory` lets the reviewer page through and filter lookups as needed instead of automatically
   inserting the entire history.

An optional model summary is only an explicitly untrusted navigation aid. The reviewer has no hidden
evaluator feedback, so review improves the verification process but does not guarantee a correct patch.

## Current Invariants

- the registry name matches the schema function name;
- `tools.enabled` controls both advertisement and authorization;
- arguments must parse as a JSON object;
- every call ID has an observation;
- remaining side-effecting calls do not run after submit or a limit;
- file I/O goes through Environment;
- observations have a character budget;
- trajectory queries are read-only and bounded;
- an assistant summary is not treated as machine evidence.

## Explicit Costs

Function calling binds the project to compatible provider formats; dedicated tools increase schema
surface area; review adds one or more model calls; and complete events increase disk and privacy
burdens. The project accepts these costs because protocol auditability and failure diagnosis are part
of its teaching goals.

See the [Tool System](../architecture/tool-system.md) for the current implementation and the
[Tool Reference](../reference/tools.md) for parameters.

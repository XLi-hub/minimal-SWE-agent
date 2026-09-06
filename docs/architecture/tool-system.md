# Tool system

The tool layer turns the function calls declared by the model into runtime operations constrained by
configuration. The public entry point remains [`tools.py`](../../src/mini_agent/tools.py); reusable
details have been split into `tooling/`.

## Module breakdown

| File | Responsibility |
|---|---|
| `tools.py` | Handlers, registry, enabled-tool selection, dispatch, and appending tool messages |
| `tooling/schemas.py` | OpenAI function schemas |
| `tooling/types.py` | `ToolDefinition`, `ToolContext`, and `ToolResult` |
| `tooling/files.py` | Unique replacement and line-numbered read formatting |
| `tooling/output.py` | Execution-result formatting, timeout decoding, and line/character truncation |
| `tooling/network.py` | Detection of obvious network commands |

This breakdown keeps `tools.py` in its "runtime orchestration" role and preserves historical import
paths; pure logic can be tested independently of Agent and a real environment.

## Single registry

Each item in `TOOL_REGISTRY` is a `ToolDefinition(name, schema, handler)`. Construction verifies that
the function name in the schema matches the registry name and rejects duplicate registrations.

The configured `tools.enabled` list stores names only, but controls two things at once:

1. `get_enabled_tool_schemas()` sends only enabled schemas to the model;
2. `execute_tool_call()` allows only handlers from the same list to execute.

This prevents "hidden from the model" and "not authorized at runtime" from being maintained as two
separate tables that can drift. An unknown configured name fails while schemas are prepared; an
unknown or disabled tool invented by the model produces a structured error observation.

## Dispatch flow

```text
ToolCall(id, function.name, function.arguments)
  ├─ look up the registry
  ├─ check tools.enabled
  ├─ decode JSON, requiring a top-level object
  ├─ construct ToolContext(environment, config, event_log)
  ├─ call the handler
  └─ append role=tool + the same tool_call_id
```

The handler returns `ToolResult(content, submission=None)`. submit expresses a terminal outcome
through the `submission` field, while ordinary tools return observation text only. Exceptions are
caught as error observations, giving the model a chance to correct its arguments or change strategy.

## Built-in tools

- `bash`: calls `Environment.execute()` and returns output, return code, and execution error;
- `read`: calls `Environment.read_file()` and supports a starting line and line-count limit;
- `edit`: reads a file first, requires `old_string` to occur exactly once, and writes it back;
- `write`: calls `Environment.write_file()` to create or overwrite a file;
- `submit`: submits an answer or patch;
- `trajectory`: read-only queries the current append-only event journal and is disabled by default in the ordinary profile.

See the [tool reference](../reference/tools.md) for parameter details.

## Output budgets

Model input must guard against two kinds of large output at once: many lines and a single very long
line. bash/read therefore apply the configured `default_max_lines` and `default_max_chars`.
Truncation preserves the beginning and end along with an omission notice; error text and timeout
messages also use the character limit, preventing model-controlled paths or exceptions from evading
the budget.

A non-zero return code in `ExecutionResult` is a normal command result and does not mean an
executor exception. Mechanical execution problems such as environment startup failures and timeouts
use `returncode=-1` and `exception_info`; the final observation shows all three dimensions clearly.

## File-tool semantics

File tools do not use the host `Path` directly; they go through Environment:

```text
read ──► environment.read_file
edit ──► read_file ──► apply_edit ──► write_file
write ──► environment.write_file
```

This makes `bash` and file tools observe the same worktree in local and Docker environments. The
unique-match requirement for `edit` prevents ambiguous replacements; zero or multiple matches return
an error rather than guessing the target.

## Network policy

When `environment.block_network_commands=true`, the bash handler detects common download, remote Git,
and package-manager commands before execution and returns a policy error. The bash description sent
to the model is also changed to describe offline semantics.

This detector is not a security sandbox: indirect commands, dynamic interpreter code, or uncovered
programs may still access the network. Docker `--network=none` provides the actual network boundary
for SWE-bench; command detection provides earlier, clearer feedback.

## The special submit path

submit is a registered tool, but Agent controls its effect:

- ordinary mode: the handler returns a submission, and the dispatcher appends an acknowledgement and raises `Submitted`;
- review mode: the dispatcher returns only a draft string, and Agent injects the review context;
- calls after submit in the same batch: append `Skipped` without executing the handler;
- a limit hit after a query: append a limit-specific `Skipped` for every declared call.

This keeps the termination intent and tool protocol valid at the same time.

## Extending the tool set

1. define the schema in `tooling/schemas.py`;
2. write a handler in `tools.py` that accepts `(args, ToolContext)`;
3. combine the two into a `ToolDefinition` and add it to the registry;
4. select it in `tools.enabled` in the target YAML profile;
5. test schema/name consistency, argument errors, the success path, permissions, and output boundaries.

Do not merely add the schema to the outbound list or add a name-based branch in Agent. The registry
is the single mapping point.

Related pages: [Agent loop](agent-loop.md), [Tool reference](../reference/tools.md),
[Tool-calling evolution](../decisions/tool-calling-evolution.md).

# Tool Reference

The authoritative source for built-in tool schemas is
[`tooling/schemas.py`](../../src/mini_agent/tooling/schemas.py); handlers and the registry are in
[`tools.py`](../../src/mini_agent/tools.py). YAML's `tools.enabled` determines which tools are visible
and executable.

## bash

```text
bash(command: string, lines?: integer, timeout?: integer)
```

- `command` is required and runs in the current Environment;
- `lines` overrides the maximum number of lines in this observation;
- `timeout` overrides the number of seconds to wait for this call, but cannot exceed the remaining
  run-level `max_time` budget;
- the return format includes output, return code, and execution exception;
- output is also subject to the configured character budget.

Normal nonzero exits preserve the real return code. A timeout or mechanical execution error usually
returns `returncode=-1` and `exception_info`. The offline SWE-bench profile rejects obvious network
commands and changes the schema description to indicate offline operation.

## read

```text
read(path: string, line_start?: integer >= 1, lines?: integer >= 1)
```

Reads a UTF-8 file from Environment and adds line numbers. `line_start` is 1-based and defaults to
the first line; follow the prompt to continue with the next chunk instead of repeatedly reading a
large file in full. Missing files, directory paths, and I/O errors become observations.

Both success and error text are subject to the character budget.

## edit

```text
edit(path: string, old_string: string, new_string: string)
```

Reads a file, requires `old_string` to occur exactly once, and writes it back through Environment.
Zero matches and multiple matches both fail; include enough context to make the target unique. An
empty `new_string` means deletion. This tool does not perform fuzzy matching, patch parsing, or
automatic formatting.

## write

```text
write(path: string, content: string)
```

Creates or replaces a complete file; the concrete Environment ensures that the parent directory
exists. It is not append and does not check whether the file already exists; the model or caller must
read it first before overwriting.

## submit

```text
submit(output: string)
```

Expresses task completion. An ordinary task can submit an answer, patch, or summary; the SWE-bench
prompt requires a complete unified diff. When review is enabled, the first submit is only a draft and
the second terminates the run.

Tools after submit in the same assistant batch receive a skipped observation and do not execute side
effects.

## trajectory

```text
trajectory(
  query?: string,
  start?: integer >= 0,
  events?: integer 1..50,
  event_type?: string,
  role?: string,
  tool_name?: string,
  returncode?: integer
)
```

Read-only search of append-only events from the current run:

- `query` performs a case-insensitive search in serialized events;
- `start` is the first sequence to consider;
- `events` limits the number of matching entries;
- `event_type`, `role`, `tool_name`, and `returncode` are exact filters;
- multiple conditions are combined with AND;
- a tool result can be linked back to `tool_name` through its call ID.

Both result count and character count are bounded, and the response includes a `start` hint for
continuing pagination. This tool is enabled by default only in the SWE-bench review profile. It
exposes raw events, in which assistant reasoning remains an untrusted claim.

## General Error Semantics

The dispatcher still appends a `role=tool` error with the matching call ID in these cases:

- the tool is not registered;
- the tool is registered but disabled by the current profile;
- `function.arguments` is not valid JSON;
- the top-level JSON value is not an object;
- a required argument is missing or has an invalid type/range;
- the handler raises an exception.

After seeing the error, the model can correct itself in the next round. Protocol integrity does not
imply operation success; consumers must inspect the observation.

## Output Truncation

bash and read select lines according to the line budget first, then apply the character budget; an
overlong single line cannot bypass the protection. The truncation notice explains how much was omitted
and how to read the next portion. trajectory also applies the character budget. Success confirmations
from edit/write remain short, while handler exceptions use the same unified tool-result channel.

## ToolContext and ToolResult

Handlers receive `ToolContext(environment, config, event_log)`:

- environment tools use `environment`;
- handlers read default budgets and policies from `config`;
- only trajectory uses `event_log`; ordinary environment tools do not depend on it.

Handlers return `ToolResult(content, submission=None)`. Only submit sets `submission`; whether the run
exits immediately is determined by the dispatcher and Agent's review state.

## Extension Checklist

1. The schema name exactly matches the registry key;
2. The handler does not bypass Environment for file I/O;
3. The configured list limits both schemas and execution;
4. Every argument error has an observation with the same call ID;
5. Large output has an explicit budget;
6. Side-effecting tools do not run after submit or a limit;
7. Add focused tests and update this page.

See the [Tool System](../architecture/tool-system.md) for design details.

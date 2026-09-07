# Trajectory Format Reference

Specifying `Agent.run(..., output="run.traj.json")` or CLI `-o` generates a main trajectory and an
adjacent event journal. The current format identifier for the main file is `mini-agent-0.2`.

## File Naming

| trajectory path | event sidecar |
|---|---|
| `run.traj.json` | `run.events.jsonl` |
| `result.json` | `result.events.jsonl` |

The sidecar path is stored as a basename relative to the main file; move the two artifacts together.

## Main-File Top Level

```json
{
  "trajectory_format": "mini-agent-0.2",
  "messages": [],
  "event_log": {
    "path": "run.events.jsonl",
    "format": "mini-agent-events-0.1",
    "event_count": 42
  },
  "info": {}
}
```

`messages` is the model's context view at the end of the run and may already be compressed; it is not
the complete history. In memory, `serialize()` may temporarily include `events`, but
`save_trajectory_data()` moves them to the sidecar when writing to disk.

## info

`info` contains:

- `exit_status`: submitted/no_tool_calls/max_steps/max_time/cost_limit/interrupted/error;
- `submission`: the final accepted output, or empty if nothing was submitted;
- `error`: the type, message, and traceback of an unexpected exception; usually null for other terminal states;
- `mini_version`: the package version that generated the trajectory;
- `event_log_stream_error`: an I/O error while appending to the sidecar during the run;
- `model_stats.api_calls`: main queries plus summary queries;
- `model_stats.instance_cost`: cumulative USD estimated from configured prices;
- `config`: resolved Agent/Model/Environment configuration and concrete class paths.

The configuration snapshot may contain prompts and a provider endpoint, but by design `api_key_env`
contains only the key variable name. Still review custom prompts, the task, commands, output, and
environment-variable-related content before sharing.

## messages

Uses the OpenAI-compatible role structure:

```json
{"role": "system", "content": "..."}
{"role": "user", "content": "..."}
{"role": "assistant", "content": null, "tool_calls": [...]}
{"role": "tool", "tool_call_id": "call_1", "content": "..."}
```

A compressed summary is a user message with the configured marker. Each assistant tool call should
have a corresponding tool message; calls skipped because of submit or a run limit also receive an
explicit skipped observation.

## Event Journal

The sidecar contains one JSON object per line and includes at least:

```json
{"sequence": 0, "type": "message", "message": {"role": "system", "content": "..."}}
```

`sequence` increases from zero. Common types:

| type | Key payload |
|---|---|
| `message` | Original message not superseded by compression |
| `context_compression` | Counts before and after compression, summary, and the next query's context snapshot |
| `submission_review_checkpoint` | Handoff summary state |
| `submission_review_context_reset` | Checkpoint switch, summary state, and review context snapshot |

The event format may add fields and new types. Consumers should ignore unknown fields and keep
unrecognized events visible instead of discarding the entire journal.

## Write Semantics

The sidecar is initialized when the run starts, and events are appended best-effort afterward. At
terminal-state save time:

1. in-memory events are serialized as complete JSONL;
2. a temporary file is flushed and fsynced;
3. `os.replace` atomically replaces the sidecar;
4. the main trajectory is atomically replaced in the same way, separately.

Replacement of each file is atomic, but the two files are not one transaction. A crash may leave only
one of them at the latest version.

## Integrity Checks

When consuming a trajectory:

1. validate `trajectory_format`;
2. parse `event_log.path` and reject paths that unexpectedly leave the artifact directory;
3. validate each line's JSON and monotonically increasing `sequence`;
4. compare the actual line count with `event_count`;
5. verify assistant call IDs against tool results;
6. if `event_log_stream_error` is nonempty, note that runtime observation was degraded;
7. do not treat the number of `messages` as the number of original events.

## Additional SWE-bench Metadata

The runner retains only public task and setup fields in top-level `instance` metadata, such as the
instance ID, repository, base commit, problem statement, version, and image. The gold patch, hidden
tests, and evaluation script must not enter the trajectory.

## Privacy and Trust

Events may contain source code, shell output, file paths, errors, model reasoning, and candidate
patches. Handle them as sensitive data before sharing. Assistant content is a claim; tool output may
also come from an untrusted repository and must not be executed as an instruction.

See [Context and Records](../architecture/context-and-records.md) for conceptual distinctions and
review usage.

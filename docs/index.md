# Documentation home

> **Simplified Chinese version:** [zh-CN/index.md](zh-CN/index.md)

The documentation is organized into architecture, guides, reference, decisions, and experiments.
The README handles orientation and startup; this page provides three reading paths. The directory
structure is at most two levels deep, and pages link to one another instead of duplicating the same
explanations.

## Path 1: Understand the system

This path is for a first read of the code, moving from the big picture to the key mechanisms.

1. [Architecture overview](architecture/overview.md): the seven parts, dependency directions, and two execution flows;
2. [Agent loop](architecture/agent-loop.md): budgets, queries, tool batches, and submit review;
3. [Tool system](architecture/tool-system.md): the registry, schema/handler pair, and protocol invariants;
4. [Models and environments](architecture/model-and-environments.md): duck typing, ABCs, local, and Docker;
5. [Context and records](architecture/context-and-records.md): messages, events, evidence, and persistence;
6. [Benchmark layer](architecture/benchmark-layer.md): the boundary around the runner and `_swebench/`.

After reading, use the [glossary](reference/glossary.md) to fill in concepts such as ABCs, dependency
injection, mocks, and round trips.
The editable architecture sources and export conventions are described in
[Diagram maintenance](diagrams/README.md).

## Path 2: Run and modify

This path is for using the project locally, changing configuration, adding tools, or running tests.

1. [Configuration guide](guides/configuration.md): configuration overrides and secret protection;
2. [Configuration reference](reference/configuration.md): field responsibilities across the six configuration sections;
3. [Tool reference](reference/tools.md): built-in tool parameters, return values, and extension steps;
4. [Testing guide](guides/testing.md): choosing among unit, integration, Docker, and E2E tests;
5. [SWE-bench guide](guides/swebench.md): single instances, batching, and official scoring.

The YAML files in the repository are authoritative for defaults; this site does not maintain a
second complete copy:

- [Ordinary profile](../src/mini_agent/config/default.yaml)
- [SWE-bench profile](../src/mini_agent/config/benchmarks/swebench.yaml)

## Path 3: Audit and revisit design

This path is for analyzing why a run succeeded or failed and understanding the reasoning behind
current trade-offs.

1. [Trajectory format](reference/trajectory-format.md): the context view, event journal, and metadata;
2. [Context and records](architecture/context-and-records.md): compression, evidence checkpoints, and lookups;
3. [Tool-calling evolution](decisions/tool-calling-evolution.md): from text parsing to the registry and review;
4. [Design trade-offs](decisions/design-tradeoffs.md): security, budgets, shell sessions, and testing boundaries;
5. [Experiment index](experiments/index.md): SWE-bench experiments and failure retrospectives.

When auditing, first distinguish between what the model said and what the machine observed.
Assistant text is a claim; tool calls, return codes, file operations, and harness reports are the
evidence that can be independently checked.

## Page map

```text
architecture/  why the system is split this way and how the runtime flows
diagrams/      cross-module draw.io sources and published SVG renders
guides/        how to configure, test, and run benchmarks
reference/     lookup pages for fields, tools, file formats, and terminology
decisions/     historical evolution and explicitly accepted trade-offs
experiments/   time-bound experiment records, not the current behavioral specification
```

If the documentation conflicts with the implementation, treat the source code, configuration
models, and YAML as authoritative, and consider the discrepancy a documentation bug to fix.

## Quick lookup

| I want to know | Go to |
|---|---|
| Why a query checks the budget both before and after tools | [Agent loop](architecture/agent-loop.md) |
| How schemas, handlers, and the enabled list correspond | [Tool system](architecture/tool-system.md) |
| File path semantics in Local and Docker | [Models and environments](architecture/model-and-environments.md) |
| How to find the original commands after compression | [Context and records](architecture/context-and-records.md) |
| Who saves predictions, statuses, and trajectories | [Benchmark layer](architecture/benchmark-layer.md) |
| What values a YAML field accepts | [Configuration reference](reference/configuration.md) |
| What parameters a tool accepts | [Tool reference](reference/tools.md) |
| The difference between `.traj.json` and `.events.jsonl` | [Trajectory format](reference/trajectory-format.md) |
| Why there is no persistent shell or background task system | [Design trade-offs](decisions/design-tradeoffs.md) |

## Documentation maintenance conventions

- For behavior descriptions, link to source code or authoritative YAML instead of copying the complete default configuration;
- experiments record observations from that time and do not redefine the current architecture;
- new modules require an update to the architecture map, while new fields, tools, and formats require an update to the corresponding reference page;
- maintain `.drawio` and `.svg` together for cross-module diagrams; use Mermaid for short in-page flows;
- `tests/test_docs.py` checks local Markdown links in the README and all docs;
- title-slug anchors are not validated, so manually check cross-page anchors when renaming titles.

Return to the [project README](../README.md).

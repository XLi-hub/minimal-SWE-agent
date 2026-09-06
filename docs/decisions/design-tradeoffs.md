# Design Trade-offs

This page summarizes the project's explicit choices and unimplemented scope. Numeric defaults may
change, so check the YAML; this page records why these mechanisms are needed and what they do not
solve.

## Minimal Does Not Mean a Single File

Separating Agent, Model, Environment, tooling, context/records, and benchmark makes each layer
replaceable and independently testable. The cost is more files; the benefit is that failures can be
localized to the protocol, provider, execution environment, or runner instead of requiring an
expensive E2E run to guess the cause.

`tools.py` and `benchmarks/swebench.py` retain the facade/orchestration roles, while their internal
details are moved into `tooling/` and `_swebench/`, respectively. This better matches the current
complexity than continuously expanding a single file.

## Model Duck Typing, Environment ABC

Model provider responses are already constrained to an OpenAI-compatible shape, and test fakes are
often very small, so Agent calls them through the `.query()` duck-typed interface. Environment has
multiple implementations involving commands, files, and cleanup, so an explicit ABC can detect
missing implementations when an instance is created.

There is no contradiction: the interface form depends on replacement risk, not on a desire for
symmetry.

## Independent Shells, Not Persistent Sessions

Each bash call starts an independent shell. This keeps timeout, output, and return-code boundaries
clear, and gives Docker exec semantics close to Local without hidden shell cwd/export state.

The trade-off is that `cd` and `export` do not persist across calls; the model must write an explicit
`cd ... && ...` into the command. The container filesystem and background processes may persist, but
the shell session itself does not.

## Local by Default, Docker for Isolation

Local starts quickly and is suitable for teaching and integration tests, but it has the host user's
permissions directly. Docker isolates files and processes, but the daemon, mounts, images, and
forwarded secrets still form an attack surface.

The project does not call Docker an absolute sandbox. The ordinary profile does not enforce network
isolation; SWE-bench uses `network=none` as a hard boundary and uses command recognition to provide
understandable early feedback.

## Synchronous Commands, Not a Background Task System

Synchronous calls keep event history linear, avoid thread locks for message writes, and make timeout
and cleanup easier to reason about. Long training runs or large builds can be backgrounded by the
command itself and write logs, but Agent does not provide task handles, notifications, or streaming
monitoring.

This limits general-purpose automation, but fits the scope of SWE tasks centered on code inspection,
editing, and testing.

## Three Budgets, Not a Single Ceiling

- max steps limits model decision rounds;
- max time limits total wall-clock time;
- cost limit limits estimated dollars based on usage and configured prices.

They cover different failure modes. A synchronous provider request cannot be preempted by the
overall wall-clock check; commands have their own timeout. When the default prices are zero, the cost
limit is ineffective, so time and step limits remain necessary safeguards.

## Function Calling, Not Text Regex

Function calling provides call IDs, names, and argument boundaries, reducing format parsing. The
trade-off is a dependency on a compatible API, and arguments still require validation. The project
chooses an explicit protocol; see [Tool-Calling Evolution](tool-calling-evolution.md) for details.

## Dedicated File Tools While Keeping bash

read/edit/write avoid shell quoting and ambiguous `sed` behavior, and can align local/Docker
semantics through Environment. bash remains useful for search, testing, and version control. Keeping
both increases tool-selection complexity, but expresses operation intent and failure semantics more
clearly.

## Approximate Tokens, Not a Provider Tokenizer

Context triggering only needs a conservative approximation, while real billing uses response usage.
Adding a tokenizer for every provider would add dependencies and model-mapping complexity, and could
still differ from the server's calculation.

The approximation may compress too early or too late; reserve and threshold values leave room for
error. Estimated values must not be used for billing.

## Separate messages and events

Working context must be compressible, while audit records must retain the original events. A single
list cannot satisfy both requirements. The two views add storage and comprehension costs, but answer
two different questions: “What did the model see most recently?” and “What actually happened?”

The event journal is still not a transactional database: streaming writes are best-effort, and the
final files are atomic individually but not transactional across files.

## Review Reduces Anchoring, But Does Not Pretend to Independently Validate

Clean-context review discards the author's working context and carries the candidate patch, machine
evidence, and an optional untrusted summary. This can prompt a fresh check, but the review may still
be performed by the same model and has no hidden-test feedback.

It is therefore a submission gate and an experimental variable, not a proof of correctness. Final
evidence still comes from independent behavioral checks and the official harness.

## Testing Pyramid

Unit/fake tests quickly cover branches, a real Local shell verifies quoting and process behavior,
Docker verifies resource lifecycles, and a real provider E2E run verifies final compatibility. Paid
E2E runs are excluded by default to prevent a key that happens to be present on the machine from
incurring charges.

Mocks cannot prove real integration, and E2E tests should not carry every boundary branch; the layers
balance cost and confidence.

## Prompt Rules

The system and instance prompts specify exploration, minimal changes, error recovery, verification,
and submission conventions. These rules reduce common failures, but may bias model behavior toward a
particular workflow.

Prompts live in YAML, allowing ordinary use and benchmarks to evolve separately. Tests should verify
protocol boundaries rather than treat prompt text itself as a reliable security mechanism.

## Non-goals

- Native SDK abstractions for multiple providers;
- a production TUI, remote queue, or background task manager;
- a complete operating-system-level sandbox;
- automatically proving a patch correct;
- claiming general capabilities from a small number of SWE-bench samples;
- treating historical experiment conclusions as the current default configuration.

See the [Architecture Overview](../architecture/overview.md) for the system as a whole, and the
[Experiment Index](../experiments/index.md) for experimental conclusions.

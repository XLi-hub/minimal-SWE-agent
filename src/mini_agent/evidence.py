"""Build compact, evidence-only checkpoints from an agent event journal.

The event journal is intentionally append-only and may contain provider-specific
objects.  This module does not call a model or interpret the author's claims;
it extracts the small amount of machine-observable evidence that is useful when
switching contexts (commands, tool results, files, and submit boundaries).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_CHARS = 12_000
DEFAULT_MAX_EVENTS = 80
_MISSING = object()


@dataclass(frozen=True)
class EventFact:
    """One normalized, machine-observable fact from a journal event."""

    sequence: str
    kind: str
    event_type: str = ""
    role: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    linked_sequence: str = ""
    command: str = ""
    file_path: str = ""
    returncode: Any = None
    has_returncode: bool = False
    error: str = ""
    output: str = ""
    detail: str = ""


@dataclass(frozen=True)
class EventEvidence:
    """Normalized event facts and counts shared by checkpoint consumers."""

    event_count: int
    first_sequence: str
    last_sequence: str
    facts: tuple[EventFact, ...]
    files: tuple[str, ...]
    tool_calls: int
    tool_results: int
    commands: int
    return_codes: int
    errors: int
    draft_boundaries: int
    assistant_claims_omitted: int
    malformed_events: int


def extract_event_evidence(
    events: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None,
) -> EventEvidence:
    """Normalize append-only events without retaining assistant narratives.

    The returned facts are deliberately provider-neutral and keep source
    sequence ids.  Consumers such as checkpoint rendering and trajectory
    filters can share this parser without making model/API calls.
    """

    raw_events = _materialize_events(events)
    facts: list[EventFact] = []
    files: set[str] = set()
    commands = tool_calls = tool_results = return_codes = errors = 0
    draft_boundaries = assistant_claims_omitted = malformed = 0
    calls_by_id: dict[str, tuple[str, str]] = {}

    for index, raw_event in enumerate(raw_events):
        event, valid = _mapping_or_empty(raw_event)
        sequence = _event_sequence(event, index)
        if not valid:
            malformed += 1
            facts.append(EventFact(sequence=sequence, kind="malformed"))
            continue

        event_type = _safe_text(_get(event, "type", ""), 48).casefold()
        message, message_valid = _mapping_or_empty(_get(event, "message", None))
        source = message if message_valid else event
        role = _safe_text(_get(source, "role", _get(event, "role", "")), 32).casefold()

        calls = _extract_tool_calls(source)
        if not calls and event_type in {"tool_call", "tool_request", "function_call"}:
            calls = [_extract_tool_call(event)]
        for call in calls:
            tool_calls += 1
            name = call["name"]
            call_id = call["id"]
            args = call["arguments"]
            command = ""
            file_path = ""
            if name == "bash":
                command_value = _argument(args, "command")
                if command_value is not _MISSING:
                    commands += 1
                    command = _inline(command_value, 320)
            if _is_file_tool(name):
                path = _argument(args, "path")
                if path is _MISSING:
                    path = _argument(args, "file")
                if path is not _MISSING:
                    file_path = _inline(path, 180)
                    files.add(file_path)
            facts.append(
                EventFact(
                    sequence=sequence,
                    kind="tool_call",
                    event_type=event_type,
                    role=role,
                    tool_name=name,
                    tool_call_id=call_id,
                    command=command,
                    file_path=file_path,
                )
            )
            if call_id:
                calls_by_id[call_id] = (name, sequence)

        result = _extract_tool_result(event, source, role, event_type)
        if result is not None:
            tool_results += 1
            result_id, code, error, output = result
            linked_name, linked_sequence = calls_by_id.get(result_id, ("", ""))
            has_code = code is not _MISSING
            return_codes += int(has_code)
            errors += int(bool(error))
            facts.append(
                EventFact(
                    sequence=sequence,
                    kind="tool_result",
                    event_type=event_type,
                    role=role,
                    tool_name=linked_name,
                    tool_call_id=result_id,
                    linked_sequence=linked_sequence,
                    returncode=None if code is _MISSING else code,
                    has_returncode=has_code,
                    error=error,
                    output=output,
                )
            )

        if event_type == "submission_review_context_reset":
            draft_boundaries += 1
            facts.append(
                EventFact(
                    sequence=sequence,
                    kind="draft_boundary",
                    event_type=event_type,
                    role=role,
                )
            )
        elif event_type == "context_compression":
            before = _get(event, "messages_before", _MISSING)
            after = _get(event, "messages_after", _MISSING)
            detail = "context compression checkpoint"
            if before is not _MISSING and after is not _MISSING:
                detail += f" ({_inline(before, 24)} -> {_inline(after, 24)} messages)"
            facts.append(
                EventFact(
                    sequence=sequence,
                    kind="context_compression",
                    event_type=event_type,
                    role=role,
                    detail=detail,
                )
            )

        if role == "assistant" and not calls:
            assistant_claims_omitted += 1

        if not calls and result is None and event_type not in {
            "submission_review_context_reset", "context_compression", "message", "",
        }:
            facts.append(
                EventFact(
                    sequence=sequence,
                    kind="unsupported",
                    event_type=event_type,
                    role=role,
                    detail=f"unsupported event type `{_inline(event_type, 48)}`",
                )
            )

    sequences = [_event_sequence(_mapping_or_empty(item)[0], i) for i, item in enumerate(raw_events)]
    return EventEvidence(
        event_count=len(raw_events),
        first_sequence=sequences[0] if sequences else "none",
        last_sequence=sequences[-1] if sequences else "none",
        facts=tuple(facts),
        files=tuple(sorted(files)),
        tool_calls=tool_calls,
        tool_results=tool_results,
        commands=commands,
        return_codes=return_codes,
        errors=errors,
        draft_boundaries=draft_boundaries,
        assistant_claims_omitted=assistant_claims_omitted,
        malformed_events=malformed,
    )


def build_review_checkpoint(
    events: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> str:
    """Return a deterministic, bounded Markdown checkpoint for *events*.

    Only structured facts are included by default.  Assistant narrative and
    reasoning are omitted (and counted as untrusted) so a context reset does
    not reintroduce the author's conclusions as if they were evidence.  Each
    extracted line keeps the source event's sequence id, allowing a model to
    use a separate trajectory lookup tool for the original record.

    ``max_events`` bounds extracted evidence lines before the character bound
    is applied.  When either bound omits data, the checkpoint reports the
    omission count and the first/last sequence ids where possible.
    """

    char_limit = _nonnegative_int(max_chars, DEFAULT_MAX_CHARS)
    event_limit = max(1, _nonnegative_int(max_events, DEFAULT_MAX_EVENTS))
    normalized = extract_event_evidence(events)
    evidence = [_fact_to_line(fact) for fact in normalized.facts]
    total_evidence = len(evidence)
    selected, event_omitted = _select_evidence(evidence, event_limit)
    shown_evidence = total_evidence - event_omitted

    summary = [
        "# Review checkpoint",
        "- Machine-extracted evidence only; assistant reasoning and claims are untrusted and omitted.",
        (
            f"- Events: {normalized.event_count} (sequence {normalized.first_sequence} -> {normalized.last_sequence}); "
            f"evidence items: {total_evidence}; showing {shown_evidence}; omitted {event_omitted}."
        ),
        (
            "- Evidence index: "
            f"tool calls={normalized.tool_calls}, results={normalized.tool_results}, commands={normalized.commands}, "
            f"return codes={normalized.return_codes}, errors={normalized.errors}, "
            f"draft boundaries={normalized.draft_boundaries}."
        ),
    ]
    if normalized.files:
        file_list = ", ".join(f"`{path}`" for path in normalized.files[:20])
        extra = len(normalized.files) - min(len(normalized.files), 20)
        if extra:
            file_list += f" (+{extra} more)"
        summary.append(f"- Files from read/edit/write calls: {file_list}.")
    if normalized.malformed_events:
        summary.append(
            f"- Malformed events: {normalized.malformed_events}; their raw provider data is not trusted here."
        )
    if normalized.assistant_claims_omitted:
        summary.append(
            f"- Omitted assistant-only narrative events: {normalized.assistant_claims_omitted}."
        )
    summary.append("## Event evidence (use sequence ids to retrieve full records)")

    if event_omitted:
        gap = (
            f"- [continuation] {event_omitted} evidence items omitted by the event bound; "
            f"full records remain in the append-only journal."
        )
        selected = _insert_gap_marker(selected, gap, event_omitted)

    return _render_bounded(summary, selected, char_limit, event_omitted)


def _fact_to_line(fact: EventFact) -> str:
    """Render one normalized fact without exposing assistant narrative."""

    if fact.kind == "malformed":
        return f"- event {fact.sequence}: malformed event retained only by sequence reference"
    if fact.kind == "tool_call":
        label = f"- event {fact.sequence}: tool call `{fact.tool_name or '<unknown>'}`"
        if fact.tool_call_id:
            label += f" id={fact.tool_call_id}"
        if fact.command:
            label += f"; command: `{fact.command}`"
        if fact.file_path:
            label += f"; file: `{fact.file_path}`"
        if fact.tool_name == "submit":
            label += "; candidate output omitted as untrusted"
        return label
    if fact.kind == "tool_result":
        label = f"- event {fact.sequence}: tool result"
        if fact.tool_call_id:
            label += f" id={fact.tool_call_id}"
        if fact.tool_name:
            label += f" for `{fact.tool_name}`"
        if fact.linked_sequence:
            label += f" (call event {fact.linked_sequence})"
        if fact.has_returncode:
            label += f"; returncode={_inline(fact.returncode, 48)}"
        if fact.error:
            label += f"; error: {fact.error}"
        elif fact.output:
            label += f"; output: {fact.output}"
        return label
    if fact.kind == "draft_boundary":
        return f"- event {fact.sequence}: draft submit boundary; review context reset"
    return f"- event {fact.sequence}: {fact.detail or fact.kind}"


def _materialize_events(events: Any) -> list[Any]:
    if events is None:
        return []
    if isinstance(events, (str, bytes, Mapping)):
        return [events]
    try:
        return list(events)
    except Exception:
        return [events]


def _mapping_or_empty(value: Any) -> tuple[Mapping[str, Any], bool]:
    return (value, True) if isinstance(value, Mapping) else ({}, False)


def _get(mapping: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return mapping.get(key, default)
    except Exception:
        return default


def _event_sequence(event: Mapping[str, Any], fallback: int) -> str:
    value = _get(event, "sequence", _MISSING)
    if value is _MISSING:
        return f"{fallback} (fallback)"
    return _inline(value, 64)


def _safe_text(value: Any, limit: int = 240) -> str:
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=repr)
    except Exception:
        try:
            text = repr(value)
        except Exception:
            text = "<unprintable>"
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _inline(value: Any, limit: int = 240) -> str:
    return " ".join(_safe_text(value, limit).replace("\r", " ").replace("\n", " ").split())


def _nonnegative_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, converted)


def _extract_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = _get(message, "tool_calls", [])
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    calls = []
    for item in raw:
        if isinstance(item, Mapping):
            calls.append(_extract_tool_call(item))
    return calls


def _extract_tool_call(item: Mapping[str, Any]) -> dict[str, Any]:
    function, function_valid = _mapping_or_empty(_get(item, "function", None))
    source = function if function_valid else item
    name = _inline(_get(source, "name", _get(item, "name", "")), 64)
    arguments = _get(source, "arguments", _get(item, "arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            arguments = {"_raw": arguments}
    if not isinstance(arguments, Mapping):
        arguments = {"_raw": arguments}
    call_id = _inline(_get(item, "id", ""), 64)
    return {"name": name, "id": call_id, "arguments": arguments}


def _argument(arguments: Mapping[str, Any], key: str) -> Any:
    try:
        return arguments[key] if key in arguments else _MISSING
    except Exception:
        return _MISSING


def _is_file_tool(name: str) -> bool:
    lowered = name.casefold()
    return lowered in {"read", "edit", "write", "read_file", "edit_file", "write_file"}


def _extract_tool_result(
    event: Mapping[str, Any],
    message: Mapping[str, Any],
    role: str,
    event_type: str,
) -> tuple[str, Any, str, str] | None:
    is_result = (
        role == "tool"
        or event_type in {"tool_result", "tool_response", "observation"}
        or any(_get(event, key, _MISSING) is not _MISSING for key in ("returncode", "return_code", "exit_code"))
    )
    if not is_result:
        return None
    source = message if role == "tool" else event
    content = _get(source, "content", _get(event, "content", ""))
    parsed = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            parsed = content
    parsed_map, parsed_valid = _mapping_or_empty(parsed)
    code = _MISSING
    for candidate in (event, source, parsed_map if parsed_valid else {}):
        for key in ("returncode", "return_code", "exit_code"):
            value = _get(candidate, key, _MISSING)
            if value is not _MISSING:
                code = value
                break
        if code is not _MISSING:
            break
    error = ""
    for candidate in (event, source, parsed_map if parsed_valid else {}):
        for key in ("exception_info", "error", "stderr"):
            value = _get(candidate, key, _MISSING)
            if value is not _MISSING and value not in (None, "", 0, False):
                error = _inline(value, 220)
                break
        if error:
            break
    if not error and isinstance(content, str) and content.lstrip().casefold().startswith("error"):
        error = _inline(content, 220)
    output = _get(parsed_map, "output", "") if parsed_valid else content
    result_id = _inline(_get(source, "tool_call_id", _get(event, "tool_call_id", "")), 64)
    return result_id, code, error, _inline(output, 220)


def _select_evidence(evidence: list[str], limit: int) -> tuple[list[str], int]:
    if len(evidence) <= limit:
        return list(evidence), 0
    head = limit // 2
    tail = limit - head
    omitted = len(evidence) - limit
    return evidence[:head] + evidence[-tail:], omitted


def _insert_gap_marker(lines: list[str], marker: str, omitted: int) -> list[str]:
    if not lines:
        return [marker]
    split = max(1, len(lines) // 2)
    return lines[:split] + [marker] + lines[split:]


def _render_bounded(
    summary: list[str], details: list[str], max_chars: int, pre_omitted: int
) -> str:
    if max_chars <= 0:
        return ""
    full = "\n".join(summary + details)
    if len(full) <= max_chars:
        return full

    base = "\n".join(summary)
    marker = (
        f"- [continuation] evidence output bounded at {max_chars} characters; "
        f"additional detail lines omitted. Full records remain in the append-only journal."
    )
    if len(base) >= max_chars:
        return base[:max_chars]
    lines = summary[:]
    used = len(base)
    for detail in details:
        candidate = used + 1 + len(detail)
        if candidate + 1 + len(marker) > max_chars:
            break
        lines.append(detail)
        used = candidate
    omitted_lines = len(details) - (len(lines) - len(summary))
    if omitted_lines:
        marker = (
            f"- [continuation] {omitted_lines} detail lines omitted by the character bound; "
            "full records remain in the append-only journal."
        )
        if used + 1 + len(marker) <= max_chars:
            lines.append(marker)
        else:
            # Keep the bound strict even for a very small caller budget.
            return "\n".join(lines)[:max_chars]
    return "\n".join(lines)[:max_chars]


__all__ = ["EventEvidence", "EventFact", "build_review_checkpoint", "extract_event_evidence"]

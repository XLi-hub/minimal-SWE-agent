import json

from mini_agent.evidence import (
    build_review_checkpoint,
    extract_event_evidence,
)


def _assistant_call(sequence, name, arguments, call_id="call-1"):
    return {
        "sequence": sequence,
        "type": "message",
        "message": {
            "role": "assistant",
            "content": "This is private reasoning and must not be copied.",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    }


def test_extracts_correlated_calls_results_commands_files_and_boundaries():
    events = [
        _assistant_call(10, "bash", {"command": "pytest tests/test_cpp.py"}),
        {
            "sequence": 11,
            "type": "message",
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": json.dumps(
                    {"output": "1 failed", "returncode": 1, "exception_info": ""}
                ),
            },
        },
        _assistant_call(12, "read", {"path": "src/parser.py"}, call_id="call-2"),
        _assistant_call(13, "edit", {"path": "src/parser.py"}, call_id="call-3"),
        _assistant_call(14, "write", {"path": "tests/test_parser.py"}, call_id="call-4"),
        _assistant_call(15, "submit", {"output": "untrusted patch"}, call_id="submit-1"),
        {"sequence": 16, "type": "submission_review_context_reset"},
    ]

    normalized = extract_event_evidence(events)
    assert normalized.tool_calls == 5
    assert normalized.tool_results == 1
    assert normalized.commands == 1
    assert normalized.return_codes == 1
    assert normalized.errors == 0
    assert normalized.draft_boundaries == 1
    assert normalized.files == ("src/parser.py", "tests/test_parser.py")
    assert normalized.facts[0].sequence == "10"
    assert normalized.facts[0].command == "pytest tests/test_cpp.py"
    result_fact = next(fact for fact in normalized.facts if fact.kind == "tool_result")
    assert result_fact.tool_name == "bash"
    assert result_fact.linked_sequence == "10"
    assert result_fact.event_type == "message"
    assert result_fact.role == "tool"

    checkpoint = build_review_checkpoint(events)
    assert "event 10" in checkpoint
    assert "command: `pytest tests/test_cpp.py`" in checkpoint
    assert "event 11" in checkpoint
    assert "for `bash` (call event 10)" in checkpoint
    assert "returncode=1" in checkpoint
    assert "`src/parser.py`" in checkpoint
    assert "draft submit boundary" in checkpoint
    assert "untrusted patch" not in checkpoint


def test_checkpoint_omits_assistant_claims_and_is_deterministically_bounded():
    events = [
        {
            "sequence": 0,
            "type": "message",
            "message": {
                "role": "assistant",
                "content": "Claim: the patch is definitely correct.",
            },
        }
    ] + [_assistant_call(i, "bash", {"command": f"echo {i}"}, call_id=f"c-{i}") for i in range(1, 8)]

    first = build_review_checkpoint(events, max_chars=900, max_events=3)
    second = build_review_checkpoint(events, max_chars=900, max_events=3)
    assert first == second
    assert len(first) <= 900
    assert "definitely correct" not in first
    assert "untrusted and omitted" in first
    assert "omitted" in first
    assert "continuation" in first


def test_malformed_and_provider_specific_events_do_not_raise():
    class Broken:
        def __repr__(self):
            raise RuntimeError("repr failed")

    events = [
        None,
        "not a mapping",
        {"sequence": "provider-seq", "type": "mystery", "payload": Broken()},
        {"type": "message", "message": {"role": "assistant", "content": "claim"}},
        {"sequence": 99, "type": "tool_result", "content": {"output": "ok", "returncode": 0}},
    ]

    normalized = extract_event_evidence(events)
    assert normalized.event_count == 5
    assert normalized.malformed_events == 2
    checkpoint = build_review_checkpoint(events)
    assert "event 0 (fallback)" in checkpoint
    assert "event 1 (fallback)" in checkpoint
    assert "provider-seq" in checkpoint
    assert "returncode=0" in checkpoint


def test_zero_character_budget_is_safe():
    assert build_review_checkpoint([{"sequence": 1, "type": "message"}], max_chars=0) == ""

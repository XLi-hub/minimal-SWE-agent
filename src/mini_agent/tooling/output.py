"""Pure helpers for formatting and bounding tool output."""

import json
import subprocess
from collections.abc import Mapping
from typing import Any


# Output is bounded independently of the configured line budget.  A line
# can contain an arbitrarily large amount of text (for example, a minified
# JSON file), so a line-only limit is not sufficient to protect the model
# context.  Keep this as a code-level safety limit for backwards-compatible
# configurations that do not yet expose a character setting.
DEFAULT_MAX_CHARS = 20_000


def decode_timeout_output(exc: subprocess.TimeoutExpired) -> str:
    """Extract partial output from a :class:`subprocess.TimeoutExpired` exception.

    Returns the captured stdout as a string, or a placeholder if nothing
    was captured before the timeout.
    """
    raw = exc.stdout
    if raw is None:
        return "(no output before timeout)"
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def format_execution_observation(
    result: Any,
    max_lines: int,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Format an environment result for the model, preserving execution metadata.

    New environments return ``output``, ``returncode``, and ``exception_info``
    so the model can distinguish a failing command from a successful command
    whose output merely happens to be empty.  Older/custom environments may
    still return a plain string; those retain the historical observation
    format for compatibility.
    """
    if not isinstance(result, Mapping):
        return truncate_output(str(result), max_lines, max_chars=max_chars)

    raw_output = str(result.get("output", ""))
    exception_info = str(result.get("exception_info", "") or "")
    output = truncate_output(raw_output, max_lines, max_chars=max_chars)
    # Exception details are useful but should not be able to consume the
    # entire observation when an environment returns a very large traceback.
    exception_info = truncate_output(
        exception_info,
        max_lines=2,
        max_chars=None if max_chars is None else max_chars // 4,
    )
    observation = {
        "output": output,
        "returncode": result.get("returncode", -1),
        "exception_info": exception_info,
    }
    # Compact JSON is both readable in a transcript and unambiguous for model
    # providers that treat tool content as plain text.
    rendered = json.dumps(observation, ensure_ascii=False)

    # ``max_chars`` is a limit on the serialized observation, not only on the
    # nested command output.  JSON escaping (notably newlines) can expand the
    # nested strings, so tighten them further when needed while retaining the
    # metadata fields.  Normal-sized observations take the fast path above.
    if max_chars is not None and len(rendered) > max_chars:
        for _ in range(8):
            overflow = len(rendered) - max_chars
            if overflow <= 0:
                break
            if len(observation["output"]) >= len(observation["exception_info"]):
                current = observation["output"]
                target = max(0, len(current) - max(1, overflow))
                observation["output"] = truncate_output(
                    current, max_lines, max_chars=target
                )
            else:
                current = observation["exception_info"]
                target = max(0, len(current) - max(1, overflow))
                observation["exception_info"] = truncate_output(
                    current, max_lines=2, max_chars=target
                )
            updated = json.dumps(observation, ensure_ascii=False)
            if len(updated) >= len(rendered) and target == len(current):
                break
            rendered = updated

        if len(rendered) > max_chars:
            # This only matters for an unusually tiny caller-provided budget;
            # keep the result valid JSON and preserve the execution metadata.
            minimal = {
                "output": "",
                "returncode": observation["returncode"],
                "exception_info": "",
            }
            rendered = json.dumps(minimal, ensure_ascii=False)
            if len(rendered) > max_chars:
                rendered = "{}" if max_chars >= 2 else ""
    return rendered


def truncate_output(
    output: str,
    max_lines: int,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Truncate *output* to at most *max_lines* lines and *max_chars* chars.

    When truncation happens the first ``max_lines // 2`` and last
    ``max_lines // 2`` lines are kept with an elision marker in between,
    so the model sees both the beginning and the end of the output.  The
    character limit is applied after line truncation, and also handles a
    single line that is itself larger than the budget.
    """
    if max_lines < 2:
        max_lines = 2  # minimum: 1 head + 1 tail
    if max_chars is not None:
        max_chars = max(0, max_chars)
        if max_chars == 0:
            return ""

    lines = output.splitlines()
    if len(lines) <= max_lines:
        return _truncate_by_chars(output, max_chars)

    half = max(1, max_lines // 2)
    head = lines[:half]
    tail = lines[-half:]
    elided = len(lines) - max_lines

    warning = (
        f"[... {elided} lines truncated ({len(lines)} total, {max_lines} shown) ...]\n"
        f"[WARNING: Output was truncated. To see more, re-run with a higher "
        f"'lines' value (e.g. lines={len(lines)}), or use head/tail/sed to "
        f"narrow down the output.]"
    )
    return _truncate_by_chars("\n".join(head + [warning] + tail), max_chars)


def _truncate_by_chars(output: str, max_chars: int | None) -> str:
    """Keep both ends of a string while enforcing an exact char budget."""
    if max_chars is None or len(output) <= max_chars:
        return output
    if max_chars <= 0:
        return ""

    warning = (
        f"[... {len(output) - max_chars} characters truncated ...]\n"
        "[WARNING: Output was truncated. To see more, narrow the command "
        "or request a smaller range.]"
    )
    # Two separators are needed around the warning.  If a caller asks for a
    # tiny budget, returning a prefix of the warning is the only way to honor
    # the exact bound; normal tool budgets are large enough for both ends.
    keep = max_chars - len(warning) - 2
    if keep <= 0:
        return warning[:max_chars]
    head_len = (keep + 1) // 2
    tail_len = keep // 2
    parts = [output[:head_len], warning]
    if tail_len:
        parts.append(output[-tail_len:])
    return "\n".join(parts)


__all__ = [
    "DEFAULT_MAX_CHARS",
    "decode_timeout_output",
    "format_execution_observation",
    "truncate_output",
]

"""Pure formatting and editing helpers for file-oriented tools."""

from .output import DEFAULT_MAX_CHARS, _truncate_by_chars


class EditError(ValueError):
    """Raised when an edit cannot be applied unambiguously."""


def apply_edit(content: str, old_string: str, new_string: str) -> str:
    """Replace the single occurrence of ``old_string`` in ``content``.

    Raises :class:`EditError` when ``old_string`` is empty, absent, or appears
    more than once — the model must then provide more context to disambiguate.
    """
    if old_string == "":
        raise EditError("old_string must be non-empty.")
    count = content.count(old_string)
    if count == 0:
        raise EditError(
            "old_string was not found in the file. The text must match exactly, "
            "including whitespace and indentation."
        )
    if count > 1:
        raise EditError(
            f"old_string is ambiguous: found {count} occurrences. "
            "Include more surrounding lines to make it unique."
        )
    return content.replace(old_string, new_string, 1)


def format_read_output(
    content: str,
    start_line: int = 1,
    max_lines: int | None = None,
    max_chars: int | None = DEFAULT_MAX_CHARS,
) -> str:
    """Return one numbered source range within line and character budgets."""
    if content == "":
        return _truncate_by_chars("(empty file)", max_chars)
    source_lines = content.splitlines()
    if start_line > len(source_lines):
        return _truncate_by_chars(
            f"(line_start {start_line} is beyond end of file; "
            f"file has {len(source_lines)} lines)",
            max_chars,
        )

    selected = source_lines[start_line - 1:]
    omitted = 0
    if max_lines is not None and len(selected) > max_lines:
        omitted = len(selected) - max_lines
        selected = selected[:max_lines]
    formatted = "\n".join(
        f"{i:>6}\t{line}"
        for i, line in enumerate(selected, start_line)
    )
    if omitted:
        next_line = start_line + len(selected)
        formatted += (
            f"\n[... {omitted} later lines not shown ...]\n"
            "[WARNING: Continue reading with "
            f"line_start={next_line}; do not reread the whole file.]"
        )
    return _truncate_by_chars(formatted, max_chars)


__all__ = ["EditError", "apply_edit", "format_read_output"]

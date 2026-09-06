"""Documentation structure checks that require only the Python standard library."""

from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _documentation_files() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def _local_target(raw_target: str) -> str | None:
    """Return a local path without query/anchor, or ``None`` when ignored."""

    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        # Markdown permits an optional title after a whitespace separator.
        target = target.split(maxsplit=1)[0]

    lowered = target.casefold()
    if not target or target.startswith("#"):
        return None
    if lowered.startswith(("http://", "https://", "mailto:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target) or None


def test_local_markdown_links_exist() -> None:
    missing: list[str] = []
    for document in _documentation_files():
        text = document.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in MARKDOWN_LINK.finditer(line):
                target = _local_target(match.group(1))
                if target is None:
                    continue
                path = Path(target)
                resolved = path if path.is_absolute() else document.parent / path
                if not resolved.exists():
                    source = document.relative_to(ROOT)
                    missing.append(f"{source}:{line_number}: {target}")

    assert not missing, "Missing local Markdown link targets:\n" + "\n".join(missing)

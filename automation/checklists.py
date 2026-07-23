"""Controller-owned checklist marker transitions."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class ChecklistFailure(RuntimeError):
    pass


def completed_text(original: str, line_index: int) -> str:
    lines = original.splitlines(keepends=True)
    if line_index < 0 or line_index >= len(lines):
        raise ChecklistFailure("selected checklist item is outside the checklist")
    line = lines[line_index]
    if line.count("[ ]") != 1:
        raise ChecklistFailure("selected checklist marker changed unexpectedly")
    lines[line_index] = line.replace("[ ]", "[x]", 1)
    return "".join(lines)


def require_unchanged(original: str, current: str) -> None:
    if current != original:
        raise ChecklistFailure("agent must not modify controller-owned checklist state")


def mark_completed(path: Path, original: str, line_index: int) -> str:
    completed = completed_text(original, line_index)
    target = path.resolve(strict=True)
    mode = stat.S_IMODE(target.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(completed)
        temporary.chmod(mode)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return completed

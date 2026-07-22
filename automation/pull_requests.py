"""Shared pull-request description helpers for automation agents."""

from __future__ import annotations

import json
import re
from pathlib import Path


MANUAL_UI_CHECKS_PROMPT = """
In your final response, include exactly one single-line field:
MANUAL_UI_CHECKS_JSON: ["action and expected result", ...]
Use at most five concise, diff-specific manual checks for user-visible UI behavior that remains valuable after automated validation. Each item must tell the reviewer what to do and what should happen. Use an empty array for backend-only changes or when no manual UI verification is useful.
""".strip()

_CHECKS_PATTERN = re.compile(r"^MANUAL_UI_CHECKS_JSON:\s*(\[.*\])\s*$", re.MULTILINE)
_UI_SUFFIXES = {".css", ".html", ".jsx", ".scss", ".tsx", ".vue"}
_UI_DIRECTORIES = {"app", "components", "pages", "screens", "ui", "views"}


def _clean_check(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    check = " ".join(value.split())[:300].strip()
    return check or None


def parse_manual_ui_checks(output: str) -> tuple[bool, list[str]]:
    """Parse the agent's final structured checklist field."""
    matches = list(_CHECKS_PATTERN.finditer(output))
    if not matches:
        return False, []
    try:
        values = json.loads(matches[-1].group(1))
    except json.JSONDecodeError:
        return False, []
    if not isinstance(values, list):
        return False, []
    checks = [check for value in values if (check := _clean_check(value))]
    return True, checks[:5]


def has_user_interface_changes(paths: list[str]) -> bool:
    for value in paths:
        path = Path(value)
        if path.suffix.lower() not in _UI_SUFFIXES:
            continue
        if _UI_DIRECTORIES.intersection(path.parts) or path.suffix.lower() != ".html":
            return True
    return False


def manual_ui_checks(output: str, paths: list[str], label: str) -> list[str]:
    supplied, checks = parse_manual_ui_checks(output)
    if supplied:
        return checks
    if has_user_interface_changes(paths):
        concise_label = " ".join(label.split())[:160]
        return [
            f"Open the UI affected by {concise_label}, exercise its primary interaction, "
            "and confirm the visible result and existing behavior are unchanged."
        ]
    return []


def manual_ui_section(checks: list[str]) -> str:
    lines = ["## Manual UI verification"]
    if checks:
        lines.extend(f"- [ ] {check}" for check in checks[:5])
    else:
        lines.append("- [x] No manual UI verification is required for this change.")
    return "\n".join(lines)

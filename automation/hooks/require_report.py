#!/usr/bin/env python3
"""Stop hook: refuse the first stop that carries no publication report.

Usage: require_report.py FIELD_NAME

The controller rejects a run whose transcript lacks `FIELD_NAME`, and the
workspace is cleaned up immediately afterwards, so an agent that ends its turn
early — typically while it believes it is waiting on something — throws away the
whole run. Block that stop once and tell the agent what is missing; if it stops
again the controller still fails loudly rather than publishing an unverified PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def transcript_text(path: Path) -> str:
    parts: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = entry.get("message", {}).get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) != 2:
        return 0
    field = sys.argv[1]
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("stop_hook_active"):
        return 0
    path = Path(str(payload.get("transcript_path", "")))
    if not path.is_file():
        return 0
    try:
        text = transcript_text(path)
    except OSError:
        return 0
    if f"{field}:" in text:
        return 0
    json.dump(
        {
            "decision": "block",
            "reason": (
                f"This run has not emitted a {field} line, so the controller will "
                "discard it and delete the workspace. Nothing is running in the "
                "background and no notification is coming: any command you started "
                "died with your turn. Finish the lifecycle now — re-run the "
                "repository validation command in the foreground, wait for its exit "
                f"status, then emit the single {field} line. If the work genuinely "
                "cannot be completed, emit the report describing what failed."
            ),
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

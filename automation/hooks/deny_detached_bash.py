#!/usr/bin/env python3
"""PreToolUse hook: refuse detached Bash calls in headless agent runs.

A headless `claude -p` (or `codex exec`) process exits the moment the agent ends
its turn, so a backgrounded command is killed before it can report anything and
the completion notification the agent is waiting for never arrives. Deny the
call instead, with a reason that tells the agent how to proceed.
"""

from __future__ import annotations

import json
import re
import sys

DETACHED_SHELL = re.compile(r"(^|\s)(nohup\s|setsid\s)|&\s*$|&\s*(?=[;\n])")

REASON = (
    "Detached execution is not available in this headless run: the agent process "
    "exits when your turn ends, so the background task is killed and its "
    "completion notification never arrives. Re-run the command in the foreground "
    "with a tool timeout long enough for it to finish, and use its own exit "
    "status as the verdict."
)


def violation(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "Bash":
        return False
    if tool_input.get("run_in_background"):
        return True
    return bool(DETACHED_SHELL.search(str(tool_input.get("command", ""))))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    if not violation(str(payload.get("tool_name", "")), tool_input):
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

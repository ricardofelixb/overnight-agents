#!/usr/bin/env python3
"""Install or remove the native macOS code-maintainer schedule."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation import launchd


LABEL = "com.overnight-agents.code-maintainer"
configured_schedule = launchd.configured_schedule
calendar_intervals = launchd.calendar_intervals


def definition(script_dir: Path, schedule: str) -> dict[str, Any]:
    return launchd.definition(
        label=LABEL,
        script_dir=script_dir,
        program_arguments=[
            "/usr/bin/python3",
            str(script_dir / "controller.py"),
            "--apply",
        ],
        schedule=schedule,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("launchd installation is only supported on macOS", file=sys.stderr)
        return 2
    try:
        if args.uninstall:
            print(launchd.install(LABEL, {}, uninstall=True))
            return 0
        schedule = configured_schedule(SCRIPT_DIR / "config.json")
        message = launchd.install(
            LABEL,
            definition(SCRIPT_DIR, schedule),
            uninstall=False,
        )
        print(f"{message}: {schedule}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

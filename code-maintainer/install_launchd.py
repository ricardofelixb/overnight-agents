#!/usr/bin/env python3
"""Install or remove native macOS per-project code-maintainer schedules."""

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
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from automation import launchd
from policy import ConfigurationFailure, validate_config


LEGACY_LABEL = "com.overnight-agents.code-maintainer"
LABEL_PREFIX = f"{LEGACY_LABEL}."
configured_schedule = launchd.configured_schedule
calendar_intervals = launchd.calendar_intervals
enabled_project_jobs = launchd.enabled_project_jobs


def project_label(name: str) -> str:
    return f"{LABEL_PREFIX}{name}"


def definition(script_dir: Path, project_name: str, schedule: str) -> dict[str, Any]:
    return launchd.definition(
        label=project_label(project_name),
        script_dir=script_dir,
        program_arguments=[
            "/usr/bin/python3",
            str(script_dir / "controller.py"),
            "--project",
            project_name,
            "--apply",
        ],
        schedule=schedule,
    )


def existing_labels() -> list[str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    return [path.stem for path in agents.glob(f"{LEGACY_LABEL}*.plist")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("launchd installation is only supported on macOS", file=sys.stderr)
        return 2
    try:
        stale = existing_labels()
        if args.uninstall:
            for label in sorted(set(stale) | {LEGACY_LABEL}):
                print(launchd.install(label, {}, uninstall=True))
            return 0
        config = json.loads((SCRIPT_DIR / "config.json").read_text())
        if not isinstance(config, dict):
            raise ValueError("config.json must be a JSON object")
        validate_config(config)
        desired = {
            project_label(name): (name, schedule)
            for name, schedule in enabled_project_jobs(config)
        }
        for label in sorted(set(stale) | {LEGACY_LABEL} | set(desired)):
            if label in desired:
                name, schedule = desired[label]
                message = launchd.install(
                    label,
                    definition(SCRIPT_DIR, name, schedule),
                    uninstall=False,
                )
                print(f"{message}: {name} {schedule}")
                continue
            print(launchd.install(label, {}, uninstall=True))
        if not desired:
            print("no enabled maintainer projects")
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        ConfigurationFailure,
    ) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

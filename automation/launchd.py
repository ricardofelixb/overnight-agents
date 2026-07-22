"""Shared launchd support for scheduled overnight-agent controllers."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def configured_schedule(config_path: Path) -> str:
    value = json.loads(config_path.read_text())
    schedule = value.get("schedule") if isinstance(value, dict) else None
    if not isinstance(schedule, str) or not schedule.strip():
        raise ValueError("config.json must contain a non-empty schedule")
    return schedule


def calendar_intervals(schedule: str) -> list[dict[str, int]]:
    fields = schedule.split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        raise ValueError("launchd supports daily minute/hour schedules only")
    minute_text, hours_text = fields[:2]
    if not minute_text.isdigit() or not 0 <= int(minute_text) <= 59:
        raise ValueError("schedule minute must be an integer from 0 to 59")
    hour_parts = hours_text.split(",")
    if not hour_parts or any(not part.isdigit() for part in hour_parts):
        raise ValueError("schedule hours must be a comma-separated integer list")
    hours = [int(part) for part in hour_parts]
    if len(set(hours)) != len(hours) or any(not 0 <= hour <= 23 for hour in hours):
        raise ValueError("schedule hours must be unique integers from 0 to 23")
    return [{"Hour": hour, "Minute": int(minute_text)} for hour in hours]


def definition(
    *, label: str, script_dir: Path, program_arguments: list[str], schedule: str
) -> dict[str, Any]:
    logs = script_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "launchd.log"
    return {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(script_dir),
        "EnvironmentVariables": {
            "PATH": (
                "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:"
                "/usr/sbin:/sbin:"
                f"{Path.home() / '.local/bin'}:{Path.home() / 'Library/pnpm'}"
            )
        },
        "StartCalendarInterval": calendar_intervals(schedule),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",
        "ThrottleInterval": 60,
    }


def atomic_plist(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(value, stream, sort_keys=True)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}"
        )
    return result


def install(label: str, definition_value: dict[str, Any], *, uninstall: bool) -> str:
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    domain = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", domain, str(path)], check=False)
    if uninstall:
        path.unlink(missing_ok=True)
        return f"removed {label}"
    atomic_plist(path, definition_value)
    run(["launchctl", "bootstrap", domain, str(path)])
    run(["launchctl", "enable", f"{domain}/{label}"])
    return f"installed {label}"

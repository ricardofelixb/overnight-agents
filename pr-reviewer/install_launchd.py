#!/usr/bin/env python3
"""Install or remove the macOS launchd schedules for the PR reviewer."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


LABELS = {
    "com.overnight-agents.pr-reviewer": "reconcile.log",
    "com.overnight-agents.pr-reviewer-webhook": "webhook.log",
    "com.overnight-agents.pr-reviewer-skills": "context-refresh.log",
}


def atomic_plist(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(value, stream, sort_keys=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result


def plist_base(label: str, script_dir: Path, log_path: Path) -> dict[str, Any]:
    return {
        "Label": label,
        "WorkingDirectory": str(script_dir),
        "EnvironmentVariables": {
            "PATH": f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{Path.home() / '.local/bin'}:{Path.home() / 'Library/pnpm'}",
        },
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "ProcessType": "Background",
    }


def definitions(script_dir: Path) -> dict[str, dict[str, Any]]:
    logs = script_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    python = sys.executable

    recovery = plist_base(
        "com.overnight-agents.pr-reviewer",
        script_dir,
        logs / LABELS["com.overnight-agents.pr-reviewer"],
    )
    recovery.update(
        {
            "ProgramArguments": [
                python,
                str(script_dir / "reconcile.py"),
                "--config",
                str(script_dir / "config.json"),
                "--apply",
            ],
            "StartInterval": 1800,
        }
    )

    webhook = plist_base(
        "com.overnight-agents.pr-reviewer-webhook",
        script_dir,
        logs / LABELS["com.overnight-agents.pr-reviewer-webhook"],
    )
    webhook.update(
        {
            "ProgramArguments": [
                python,
                str(script_dir / "webhook.py"),
                "--config",
                str(script_dir / "config.json"),
                "--env",
                str(script_dir / ".env"),
                "--apply",
            ],
            "KeepAlive": True,
            "RunAtLoad": True,
            "ThrottleInterval": 10,
        }
    )

    refresh = plist_base(
        "com.overnight-agents.pr-reviewer-skills",
        script_dir,
        logs / LABELS["com.overnight-agents.pr-reviewer-skills"],
    )
    refresh.update(
        {
            "ProgramArguments": [
                python,
                str(script_dir / "refresh_context.py"),
                "--config",
                str(script_dir / "config.json"),
                "--manifest",
                str(script_dir / "provider-skills.json"),
                "--state-root",
                str(script_dir / "state"),
                "--lock",
                str(script_dir / "state" / "skills.lock.json"),
            ],
            "StartCalendarInterval": {"Weekday": 0, "Hour": 3, "Minute": 15},
        }
    )
    return {
        recovery["Label"]: recovery,
        webhook["Label"]: webhook,
        refresh["Label"]: refresh,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("launchd installation is only supported on macOS", file=sys.stderr)
        return 2

    script_dir = Path(__file__).resolve().parent
    agents = Path.home() / "Library" / "LaunchAgents"
    domain = f"gui/{os.getuid()}"
    try:
        for label, value in definitions(script_dir).items():
            path = agents / f"{label}.plist"
            run(["launchctl", "bootout", domain, str(path)], check=False)
            if args.uninstall:
                path.unlink(missing_ok=True)
                print(f"removed {label}")
                continue
            atomic_plist(path, value)
            run(["launchctl", "bootstrap", domain, str(path)])
            run(["launchctl", "enable", f"{domain}/{label}"])
            print(f"installed {label}")
        return 0
    except (OSError, RuntimeError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

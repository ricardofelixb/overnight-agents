#!/usr/bin/env python3
"""Refresh audited provider skills and isolated project AI-file snapshots."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> None:
    result = subprocess.run(command, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {command[0]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--global-root", type=Path)
    args = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    refresh_skills = [
        sys.executable,
        str(script_dir / "refresh_skills.py"),
        "--manifest",
        str(args.manifest),
        "--state-root",
        str(args.state_root),
        "--lock",
        str(args.lock),
        "--promote",
    ]
    if args.global_root:
        refresh_skills.extend(["--global-root", str(args.global_root)])
    try:
        run(refresh_skills)
        run(
            [
                sys.executable,
                str(script_dir / "refresh_ai_files.py"),
                "--config",
                str(args.config),
            ]
        )
        return 0
    except RuntimeError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

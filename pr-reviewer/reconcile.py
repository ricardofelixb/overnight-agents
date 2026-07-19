#!/usr/bin/env python3
"""Recovery sweep for eligible open PRs missed by webhook delivery."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from policy import evaluate_pr_eligibility
from review import load_configuration, review_is_current
from telegram_notify import NotificationFailure, flush_pending


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config, config_dir = load_configuration(args.config)
    defaults = config.get("defaults", {})
    state_root = Path(config["state_root"]).expanduser()
    if not state_root.is_absolute():
        state_root = (config_dir / state_root).resolve()
    if defaults.get("telegram_notifications_enabled", False):
        env_path = Path(config["telegram_env"]).expanduser()
        if not env_path.is_absolute():
            env_path = (config_dir / env_path).resolve()
        try:
            delivered, failed = flush_pending(state_root, env_path)
            if delivered or failed:
                print(f"Telegram outbox retry: delivered={delivered} pending_failures={failed}")
        except NotificationFailure:
            print("Telegram outbox retry unavailable; pending events were preserved", file=sys.stderr)
    reviewer = Path(__file__).with_name("review.py")
    failures = 0
    for project in config.get("projects", []):
        if not project.get("enabled", False):
            continue
        policy = defaults | project
        listed = run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                project["repository"],
                "--state",
                "open",
                "--base",
                project["base_branch"],
                "--json",
                "number,state,baseRefName,baseRefOid,headRefName,headRefOid,headRepositoryOwner,"
                "isCrossRepository,updatedAt,author,isDraft",
            ]
        )
        if listed.returncode != 0:
            print(listed.stdout, file=sys.stderr)
            failures += 1
            continue
        for pull_request in json.loads(listed.stdout):
            if evaluate_pr_eligibility(pull_request, policy):
                continue
            if review_is_current(state_root, project["name"], pull_request):
                continue
            command = [
                sys.executable,
                str(reviewer),
                "--config",
                str(args.config),
                "--project",
                project["name"],
                "--pr",
                str(pull_request["number"]),
            ]
            if args.apply:
                command.append("--apply")
            result = run(command)
            print(result.stdout, end="")
            if result.returncode not in (0, 2):
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

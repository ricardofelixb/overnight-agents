#!/usr/bin/env python3
"""Durable, outbound-only Telegram notifications for autonomous PR reviews."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_MESSAGE_CHARS = 3900
TELEGRAM_HOST = "api.telegram.org"


class NotificationFailure(RuntimeError):
    """A notification could not be delivered without exposing credentials."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_credentials(env_path: Path) -> tuple[str, str]:
    try:
        if env_path.is_symlink():
            raise NotificationFailure("Telegram environment file must not be a symlink")
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode & 0o077:
            raise NotificationFailure("Telegram environment file permissions must be 600")
        values: dict[str, str] = {}
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[name] = value
    except OSError as error:
        raise NotificationFailure("Telegram environment file is unavailable") from error

    token = values.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = values.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise NotificationFailure("Telegram outbound credentials are incomplete")
    return token, chat_id


def _single_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def format_merge_message(event: dict[str, Any]) -> str:
    number = event.get("pr_number")
    project = _single_line(event.get("project"), 80)
    title = _single_line(event.get("title"), 180)
    base_branch = _single_line(event.get("base_branch"), 100)
    url = _single_line(event.get("url"), 500)
    head_sha = _single_line(event.get("head_sha"), 40)
    domains = sorted({_single_line(item, 40) for item in event.get("domains", []) if item})
    changed_files = [_single_line(item, 180) for item in event.get("changed_files", []) if item]
    repair_count = int(event.get("repair_count", 0))
    review_passes = int(event.get("review_passes", 0))

    lines = [
        f"✅ {project}: PR #{number} squash-merged into {base_branch}",
        title,
        url,
        "",
        f"Reviewed head: {head_sha[:12]}",
        f"Evidence: {review_passes} independent review passes, local validation, and required GitHub CI passed.",
        f"Controller repairs: {repair_count}",
        f"Provider review: {', '.join(domains) if domains else 'No configured provider domain detected'}",
    ]

    if changed_files:
        lines.extend(["", "Changed areas:"])
        lines.extend(f"• {path}" for path in changed_files[:8])
        if len(changed_files) > 8:
            lines.append(f"• …and {len(changed_files) - 8} more files")

    checks = [
        "Pull the latest base branch before doing follow-up work.",
        "Run `vercel --prod` manually when you are ready to deploy.",
        "Exercise the authenticated happy path affected by the changed files.",
    ]
    if "react" in domains:
        checks.append("Check affected UI loading, empty, error, responsive, and navigation states.")
    if "workos" in domains:
        checks.append("Verify sign-in, organization context, permissions, session refresh, and logout as applicable.")
    if "convex" in domains:
        checks.append("Verify affected Convex reads/writes with real authorization and inspect runtime logs/data.")

    lines.extend(["", "Manual sanity checks:"])
    lines.extend(f"{index}. {check}" for index, check in enumerate(checks, start=1))
    lines.extend(["", "Merge does not deploy production automatically."])
    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 20].rstrip() + "\n…message truncated"
    return message


def enqueue_notification(state_root: Path, event: dict[str, Any]) -> Path:
    if event.get("type") != "pr_merged":
        raise NotificationFailure("unsupported notification event")
    pending = state_root / "notification-outbox" / "pending"
    created = str(event.get("created_at") or now_iso()).replace(":", "").replace("+", "_")
    head = _single_line(event.get("head_sha"), 40)[:12] or "unknown"
    filename = f"{created}-pr-{int(event['pr_number'])}-{head}.json"
    path = pending / filename
    atomic_json(path, event)
    return path


def send_message(env_path: Path, message: str, timeout_seconds: int = 20) -> None:
    token, chat_id = load_credentials(env_path)
    endpoint = f"https://{TELEGRAM_HOST}/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode()
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "autonomous-pr-review/1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if urllib.parse.urlparse(response.geturl()).hostname != TELEGRAM_HOST:
                raise NotificationFailure("Telegram redirected to an unexpected host")
            payload = json.loads(response.read(1_000_001))
            if response.status != 200 or payload.get("ok") is not True:
                raise NotificationFailure("Telegram rejected the notification")
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        if isinstance(error, NotificationFailure):
            raise
        raise NotificationFailure("Telegram delivery failed") from error


def deliver_notification(event_path: Path, env_path: Path, state_root: Path) -> None:
    try:
        event = json.loads(event_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise NotificationFailure("queued Telegram event is invalid") from error
    send_message(env_path, format_merge_message(event))
    sent = state_root / "notification-outbox" / "sent" / event_path.name
    sent.parent.mkdir(parents=True, exist_ok=True)
    os.replace(event_path, sent)


def flush_pending(state_root: Path, env_path: Path) -> tuple[int, int]:
    pending = state_root / "notification-outbox" / "pending"
    if not pending.exists():
        return 0, 0
    delivered = 0
    failed = 0
    for event_path in sorted(pending.glob("*.json")):
        try:
            deliver_notification(event_path, env_path, state_root)
            delivered += 1
        except NotificationFailure:
            failed += 1
    return delivered, failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--flush", action="store_true")
    action.add_argument("--test", action="store_true")
    args = parser.parse_args()
    try:
        if args.test:
            send_message(
                args.env,
                "✅ Autonomous PR reviewer notifications are configured.\n\nThis is a send-only test; no PR was merged.",
            )
            print("Telegram test notification delivered")
            return 0
        delivered, failed = flush_pending(args.state_root, args.env)
        print(f"Telegram outbox: delivered={delivered} pending_failures={failed}")
        return 1 if failed else 0
    except NotificationFailure as error:
        print(f"BLOCKED: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

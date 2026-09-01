"""Best-effort completion reporting for every overnight-agents job."""

from __future__ import annotations

import json
import os
import stat
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


WEBHOOK_URL_ENV = "GROK_BOT_WEBHOOK_URL"
WEBHOOK_KEY_ENV = "GROK_BOT_WEBHOOK_KEY"
_logged_configuration_warnings: set[str] = set()


class CompletionReporterFailure(RuntimeError):
    """A configuration or delivery error that must not affect the job result."""


def _log_once(logger: Callable[[str], None], key: str, message: str) -> None:
    if key in _logged_configuration_warnings:
        return
    _logged_configuration_warnings.add(key)
    _safe_log(logger, message)


def _safe_log(logger: Callable[[str], None], message: str) -> None:
    try:
        logger(message)
    except Exception:
        pass


def _private_environment(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise CompletionReporterFailure("environment file must not be a symlink")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise CompletionReporterFailure("environment file permissions must be 600")
        lines = path.read_text().splitlines()
    except OSError as error:
        raise CompletionReporterFailure("environment file is unavailable") from error

    values: dict[str, str] = {}
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise CompletionReporterFailure(
                f"invalid environment assignment at line {number}"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name in {WEBHOOK_URL_ENV, WEBHOOK_KEY_ENV}:
            values[name] = value
    return values


def completion_payload(
    *,
    job: str,
    repo: str,
    status: str,
    summary: str,
    pr_url: str = "",
    commit: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable payload shared by all current and future jobs."""
    if status not in {"success", "failure"}:
        raise ValueError("completion status must be success or failure")
    payload: dict[str, Any] = {
        "job": str(job),
        "repo": str(repo),
        "status": status,
        "summary": " ".join(str(summary).split())[:1000],
        "pr_url": str(pr_url),
        "commit": str(commit),
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if key not in payload})
    return payload


def report_completion(
    *,
    job: str,
    repo: str,
    status: str,
    summary: str,
    pr_url: str = "",
    commit: str = "",
    extra: Mapping[str, Any] | None = None,
    env_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
    logger: Callable[[str], None] = print,
    timeout_seconds: int = 20,
) -> bool:
    """POST a terminal job result, swallowing all notifier-only failures."""
    try:
        values = dict(os.environ if environment is None else environment)
        if env_path is not None:
            values.update(_private_environment(env_path))
        url = values.get(WEBHOOK_URL_ENV, "").strip()
        key = values.get(WEBHOOK_KEY_ENV, "").strip()
        if not url or not key:
            _log_once(
                logger,
                "missing-configuration",
                "Completion reporter: GROK_BOT_WEBHOOK_URL or GROK_BOT_WEBHOOK_KEY "
                "is unset; skipping notification.",
            )
            return False
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise CompletionReporterFailure(
                "webhook URL must be an HTTP(S) URL without credentials"
            )

        body = json.dumps(
            completion_payload(
                job=job,
                repo=repo,
                status=status,
                summary=summary,
                pr_url=pr_url,
                commit=commit,
                extra=extra,
            ),
            separators=(",", ":"),
        ).encode()
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = (
                response.status
                if hasattr(response, "status")
                else response.getcode()
            )
            if not 200 <= status_code < 300:
                raise CompletionReporterFailure(
                    f"webhook returned HTTP {status_code}"
                )
        return True
    except Exception as error:  # Notification delivery is intentionally best effort.
        _safe_log(
            logger,
            f"Completion reporter failed; job result is unchanged: {error}",
        )
        return False

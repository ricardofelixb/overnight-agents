#!/usr/bin/env python3
"""Create or update a GitHub repository webhook without exposing its secret."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ConfigurationFailure(RuntimeError):
    pass


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    if path.exists() and path.is_symlink():
        raise ConfigurationFailure("environment file must not be a symlink")
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigurationFailure("environment file permissions must be 600")
    lines = path.read_text().splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.removeprefix("export ").strip()] = value.strip().strip("'\"")
    return lines, values


def ensure_env(path: Path, public_url: str) -> dict[str, str]:
    lines, values = read_env(path)
    desired = {
        "GITHUB_WEBHOOK_SECRET": values.get("GITHUB_WEBHOOK_SECRET") or secrets.token_hex(32),
        "GITHUB_WEBHOOK_HOST": "127.0.0.1",
        "GITHUB_WEBHOOK_PORT": "8765",
        "GITHUB_WEBHOOK_PATH": "/github-webhook",
        "GITHUB_WEBHOOK_PUBLIC_URL": public_url,
    }
    positions: dict[str, int] = {}
    for index, raw in enumerate(lines):
        line = raw.strip().removeprefix("export ").lstrip()
        if "=" in line:
            positions[line.split("=", 1)[0].strip()] = index
    if lines and lines[-1].strip():
        lines.append("")
    if not any(line.strip() == "# GitHub webhook ingress." for line in lines):
        lines.append("# GitHub webhook ingress.")
    for name, value in desired.items():
        rendered = f"{name}={value}"
        if name in positions:
            lines[positions[name]] = rendered
        else:
            lines.append(rendered)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("\n".join(lines).rstrip() + "\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return desired


def gh(command: list[str]) -> Any:
    result = subprocess.run(["gh", "api", *command], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ConfigurationFailure(f"gh api failed ({result.returncode})")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def gh_with_payload(endpoint: str, method: str, payload: dict[str, Any]) -> Any:
    descriptor, temporary = tempfile.mkstemp(prefix="pr-reviewer-hook-", suffix=".json")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream)
        return gh([endpoint, "--method", method, "--input", temporary])
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(repository: str, public_url: str, secret: str) -> dict[str, Any]:
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise ConfigurationFailure("repository must be owner/name")
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path != "/github-webhook":
        raise ConfigurationFailure("public URL must be HTTPS and end at /github-webhook")
    endpoint = f"repos/{repository}/hooks"
    hooks = gh([endpoint])
    existing = next(
        (
            hook
            for hook in hooks
            if hook.get("name") == "web" and (hook.get("config") or {}).get("url") == public_url
        ),
        None,
    )
    payload = {
        "name": "web",
        "active": True,
        "events": ["issue_comment"],
        "config": {
            "url": public_url,
            "content_type": "json",
            "insecure_ssl": "0",
            "secret": secret,
        },
    }
    if existing:
        hook = gh_with_payload(f"{endpoint}/{existing['id']}", "PATCH", payload)
        action = "updated"
    else:
        hook = gh_with_payload(endpoint, "POST", payload)
        action = "created"
    return {
        "action": action,
        "id": hook.get("id"),
        "active": hook.get("active"),
        "events": hook.get("events", []),
        "url": (hook.get("config") or {}).get("url"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--env-only", action="store_true")
    args = parser.parse_args()
    try:
        values = ensure_env(args.env, args.url)
        if args.env_only:
            print(json.dumps({"env": "configured"}))
            return 0
        result = configure(args.repository, args.url, values["GITHUB_WEBHOOK_SECRET"])
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ConfigurationFailure) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

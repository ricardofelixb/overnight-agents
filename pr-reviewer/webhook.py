#!/usr/bin/env python3
"""Authenticated GitHub webhook ingress and durable PR-review queue."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from review import ReviewFailure, load_configuration


MAX_BODY_BYTES = 2_000_000
DELIVERY_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")
DEFAULT_REVIEW_COMMANDS = ["/review"]
DEFAULT_SIMPLIFY_COMMANDS = ["/simplify"]
DEFAULT_REVIEW_AUTHOR_ASSOCIATIONS = ["OWNER", "MEMBER", "COLLABORATOR"]


class WebhookFailure(RuntimeError):
    pass


def utc_now() -> str:
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


def load_private_env(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink():
            raise WebhookFailure("webhook environment file must not be a symlink")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise WebhookFailure("webhook environment file permissions must be 600")
        values: dict[str, str] = {}
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[name.strip()] = value
        return values
    except OSError as error:
        raise WebhookFailure("webhook environment file is unavailable") from error


def signature_is_valid(secret: str, body: bytes, supplied: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(expected, supplied)


class DeliveryQueue:
    def __init__(self, state_root: Path, reviewer: Path, config_path: Path, apply: bool, log_path: Path):
        self.pending = state_root / "webhook-queue" / "pending"
        self.processing = state_root / "webhook-queue" / "processing"
        self.completed = state_root / "webhook-deliveries"
        self.reviewer = reviewer
        self.config_path = config_path
        self.apply = apply
        self.log_path = log_path
        self.lock = threading.Lock()
        self.wake = threading.Event()
        for directory in (self.pending, self.processing, self.completed):
            directory.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        for path in self.processing.glob("*.json"):
            destination = self.pending / path.name
            if destination.exists() or (self.completed / path.name).exists():
                path.unlink(missing_ok=True)
            else:
                path.replace(destination)

    def enqueue(self, delivery: str, value: dict[str, Any]) -> bool:
        if not DELIVERY_RE.fullmatch(delivery):
            raise WebhookFailure("invalid GitHub delivery identifier")
        with self.lock:
            paths = (
                self.pending / f"{delivery}.json",
                self.processing / f"{delivery}.json",
                self.completed / f"{delivery}.json",
            )
            if any(path.exists() for path in paths):
                return False
            atomic_json(paths[0], value)
            self.wake.set()
            return True

    def _next(self) -> Path | None:
        paths = sorted(self.pending.glob("*.json"), key=lambda path: (path.stat().st_mtime_ns, path.name))
        return paths[0] if paths else None

    def _run_job(self, path: Path) -> None:
        processing = self.processing / path.name
        try:
            path.replace(processing)
        except FileNotFoundError:
            return
        try:
            job = json.loads(processing.read_text())
            command = [
                sys.executable,
                str(self.reviewer),
                "--config",
                str(self.config_path),
                "--project",
                str(job["project"]),
                "--pr",
                str(job["pr_number"]),
            ]
            if self.apply:
                command.append("--apply")
            if job.get("force") is True:
                command.append("--force")
            command.extend(["--operation", str(job.get("operation", "review"))])
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as stream:
                stream.write(
                    f"[{utc_now()}] delivery={job['delivery']} project={job['project']} "
                    f"pr={job['pr_number']} action={job['action']}\n"
                )
                stream.flush()
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False, text=True)
                stream.write(f"[{utc_now()}] delivery={job['delivery']} exit={result.returncode}\n")
            atomic_json(
                self.completed / processing.name,
                job
                | {
                    "completed_at": utc_now(),
                    "review_exit_code": result.returncode,
                    "outcome": "completed" if result.returncode in {0, 2} else "failed",
                },
            )
        except (KeyError, OSError, json.JSONDecodeError) as error:
            atomic_json(
                self.completed / processing.name,
                {"completed_at": utc_now(), "outcome": "invalid", "error": type(error).__name__},
            )
        finally:
            processing.unlink(missing_ok=True)
            self._prune_completed()

    def _prune_completed(self, retain: int = 1000) -> None:
        paths = sorted(self.completed.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for path in paths[retain:]:
            path.unlink(missing_ok=True)

    def run_forever(self) -> None:
        while True:
            path = self._next()
            if path is None:
                self.wake.clear()
                if self._next() is None:
                    self.wake.wait(timeout=30)
                continue
            self._run_job(path)


class WebhookApplication:
    def __init__(
        self,
        *,
        secret: str,
        endpoint_path: str,
        projects: list[dict[str, Any]],
        queue: DeliveryQueue,
    ):
        if not secret:
            raise WebhookFailure("GITHUB_WEBHOOK_SECRET is missing")
        self.secret = secret
        self.endpoint_path = "/" + endpoint_path.strip("/")
        self.projects = {
            project["repository"].lower(): project
            for project in projects
            if project.get("enabled", False)
        }
        self.queue = queue

    def accepted_post_path(self, path: str) -> bool:
        return path in {"/", self.endpoint_path}

    def health_path(self, path: str) -> bool:
        return path in {"/healthz", f"{self.endpoint_path}/healthz"}

    def handle(self, event: str, delivery: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if event == "ping":
            return 200, {"status": "pong"}
        if event != "issue_comment":
            return 202, {"status": "ignored", "reason": "event"}
        if payload.get("action") != "created":
            return 202, {"status": "ignored", "reason": "action"}
        repository = ((payload.get("repository") or {}).get("full_name") or "").lower()
        project = self.projects.get(repository)
        if project is None:
            return 202, {"status": "ignored", "reason": "repository"}
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            return 202, {"status": "ignored", "reason": "not_pull_request"}
        if issue.get("state") != "open":
            return 202, {"status": "ignored", "reason": "pull_request_state"}
        comment = payload.get("comment") or {}
        body = str(comment.get("body", "")).strip()
        review_commands = project.get("review_comment_commands", DEFAULT_REVIEW_COMMANDS)
        simplify_commands = project.get("simplify_comment_commands", DEFAULT_SIMPLIFY_COMMANDS)
        if body in review_commands:
            operation = "review"
        elif body in simplify_commands:
            operation = "simplify"
        else:
            return 202, {"status": "ignored", "reason": "command"}
        author = ((comment.get("user") or {}).get("login") or "")
        if author in project.get("excluded_authors", []):
            return 202, {"status": "ignored", "reason": "author"}
        associations = project.get(
            "review_comment_author_associations",
            DEFAULT_REVIEW_AUTHOR_ASSOCIATIONS,
        )
        if comment.get("author_association") not in associations:
            return 202, {"status": "ignored", "reason": "authorization"}
        number = issue.get("number")
        if not isinstance(number, int) or number <= 0:
            raise WebhookFailure("pull request number is invalid")
        job = {
            "version": 1,
            "delivery": delivery,
            "received_at": utc_now(),
            "project": project["name"],
            "repository": project["repository"],
            "pr_number": number,
            "action": f"issue_comment:{operation}_command",
            "operation": operation,
            "force": True,
        }
        created = self.queue.enqueue(delivery, job)
        return 202, {"status": "queued" if created else "duplicate"}


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "OvernightWebhook/1"

    @property
    def application(self) -> WebhookApplication:
        return self.server.application  # type: ignore[attr-defined]

    def _json(self, status: int, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self.application.health_path(path):
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if not self.application.accepted_post_path(path):
            self._json(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(413, {"status": "invalid_size"})
            return
        body = self.rfile.read(length)
        signature = self.headers.get("X-Hub-Signature-256", "")
        if not signature_is_valid(self.application.secret, body, signature):
            self._json(401, {"status": "invalid_signature"})
            return
        event = self.headers.get("X-GitHub-Event", "")
        delivery = self.headers.get("X-GitHub-Delivery", "")
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise WebhookFailure("payload must be an object")
            status, response = self.application.handle(event, delivery, payload)
            self._json(status, response)
        except (json.JSONDecodeError, WebhookFailure):
            self._json(400, {"status": "invalid_payload"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{utc_now()}] {self.client_address[0]} {format % args}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        config, _ = load_configuration(args.config)
        values = load_private_env(args.env)
        host = values.get("GITHUB_WEBHOOK_HOST", "127.0.0.1")
        port = int(values.get("GITHUB_WEBHOOK_PORT", "8765"))
        endpoint_path = values.get("GITHUB_WEBHOOK_PATH", "/github-webhook")
        if host not in {"127.0.0.1", "::1"}:
            raise WebhookFailure("webhook receiver must bind to loopback")
        if not 1024 <= port <= 65535:
            raise WebhookFailure("webhook port must be between 1024 and 65535")
        state_root = Path(config["state_root"])
        queue = DeliveryQueue(
            state_root,
            Path(__file__).with_name("review.py"),
            args.config.resolve(),
            args.apply,
            Path(__file__).with_name("logs") / "webhook-worker.log",
        )
        application = WebhookApplication(
            secret=values.get("GITHUB_WEBHOOK_SECRET", ""),
            endpoint_path=endpoint_path,
            projects=[config.get("defaults", {}) | project for project in config["projects"]],
            queue=queue,
        )
        worker = threading.Thread(target=queue.run_forever, name="pr-review-worker", daemon=True)
        worker.start()
        server = ThreadingHTTPServer((host, port), WebhookHandler)
        server.application = application  # type: ignore[attr-defined]
        print(f"[{utc_now()}] webhook receiver listening on {host}:{port}", flush=True)
        server.serve_forever()
        return 0
    except (OSError, ValueError, ReviewFailure, WebhookFailure) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Idempotent GitHub reaction and progress-comment updates for queued PR jobs."""

from __future__ import annotations

import re
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable


DELIVERY_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class ProgressFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_command_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProgressFailure("GitHub progress command could not complete") from error
    if result.returncode != 0:
        raise ProgressFailure(
            f"GitHub progress command failed with exit code {result.returncode}"
        )
    return result


class GitHubProgress:
    def __init__(
        self,
        *,
        repository: str,
        pr_number: int,
        delivery: str,
        operation: str,
        command_comment_id: int,
        enabled: bool = True,
        heartbeat_seconds: int = 900,
        command_runner: CommandRunner | None = None,
        logger: Callable[[str], None] | None = None,
    ):
        if not REPOSITORY_RE.fullmatch(repository):
            raise ValueError("invalid progress repository")
        if not isinstance(pr_number, int) or pr_number <= 0:
            raise ValueError("invalid progress pull request number")
        if not DELIVERY_RE.fullmatch(delivery):
            raise ValueError("invalid progress delivery")
        if operation not in {"review", "simplify"}:
            raise ValueError("invalid progress operation")
        if not isinstance(command_comment_id, int) or command_comment_id <= 0:
            raise ValueError("invalid progress command comment")
        if not isinstance(heartbeat_seconds, int) or not 60 <= heartbeat_seconds <= 3600:
            raise ValueError("invalid progress heartbeat interval")
        self.repository = repository
        self.pr_number = pr_number
        self.delivery = delivery
        self.operation = operation
        self.command_comment_id = command_comment_id
        self.enabled = enabled
        self.heartbeat_seconds = heartbeat_seconds
        self.command_runner = command_runner or default_command_runner
        self.logger = logger or (lambda _message: None)
        self.marker = f"<!-- overnight-agents-progress:{delivery} -->"
        self._comment_id: int | None = None
        self._status = "queued"
        self._phase = "Queued"
        self._detail = "The command was accepted and durably queued."
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self.command_runner(command)

    def _best_effort(self, action: Callable[[], None], label: str) -> bool:
        if not self.enabled:
            return False
        try:
            action()
            return True
        except Exception as error:
            try:
                self.logger(f"GitHub progress {label} failed: {error}")
            except Exception:
                pass
            return False

    def _react(self) -> None:
        self._run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{self.repository}/issues/comments/{self.command_comment_id}/reactions",
                "-f",
                "content=eyes",
                "--silent",
            ]
        )

    def _find_comment_id(self) -> int | None:
        result = self._run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{self.repository}/issues/{self.pr_number}/comments",
                "--paginate",
                "-f",
                "per_page=100",
                "--jq",
                f'.[] | select((.body // "") | contains("{self.marker}")) | .id',
            ]
        )
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not values:
            return None
        try:
            return int(values[-1])
        except ValueError as error:
            raise ProgressFailure("GitHub returned an invalid progress comment id") from error

    def _safe_detail(self, value: str) -> str:
        compact = " ".join(value.split())
        return compact[:500] if compact else "Work is continuing."

    def _body(self) -> str:
        icons = {
            "queued": "⏳",
            "running": "👀",
            "complete": "✅",
            "blocked": "⚠️",
            "failed": "❌",
        }
        command = f"/{self.operation}"
        return (
            f"{self.marker}\n"
            f"### {icons[self._status]} `{command}` {self._status}\n\n"
            f"**Status:** {self._phase}\n\n"
            f"{self._safe_detail(self._detail)}\n\n"
            f"_Last updated: {utc_now()} · This comment is updated in place._"
        )

    def _publish_locked(self) -> None:
        if self._comment_id is None:
            self._comment_id = self._find_comment_id()
        body = self._body()
        if self._comment_id is None:
            result = self._run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{self.repository}/issues/{self.pr_number}/comments",
                    "-f",
                    f"body={body}",
                    "--jq",
                    ".id",
                ]
            )
            try:
                self._comment_id = int(result.stdout.strip())
            except ValueError as error:
                raise ProgressFailure(
                    "GitHub returned an invalid created progress comment id"
                ) from error
            return
        self._run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{self.repository}/issues/comments/{self._comment_id}",
                "-f",
                f"body={body}",
                "--silent",
            ]
        )

    def acknowledge_queued(self) -> None:
        self._best_effort(self._react, "reaction")
        self._best_effort(self._publish_current, "queued comment")

    def _publish_current(self) -> None:
        with self._lock:
            self._publish_locked()

    def phase(self, phase: str, detail: str) -> None:
        def update() -> None:
            with self._lock:
                self._status = "running"
                self._phase = self._safe_detail(phase)[:120]
                self._detail = detail
                self._publish_locked()

        self._best_effort(update, "phase update")

    def finish(self, status: str, phase: str, detail: str) -> None:
        if status not in {"complete", "blocked", "failed"}:
            raise ValueError("invalid terminal progress status")
        self.stop_heartbeat()

        def update() -> None:
            with self._lock:
                self._status = status
                self._phase = self._safe_detail(phase)[:120]
                self._detail = detail
                self._publish_locked()

        self._best_effort(update, "terminal update")

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            self._best_effort(self._publish_current, "heartbeat")

    def start_heartbeat(self) -> None:
        if not self.enabled or self._heartbeat_thread is not None:
            return
        self._stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat,
            name=f"github-progress-{self.delivery}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._heartbeat_thread = None


def progress_from_job(
    job: dict[str, object],
    *,
    command_runner: CommandRunner | None = None,
    logger: Callable[[str], None] | None = None,
) -> GitHubProgress | None:
    progress = job.get("progress")
    if not isinstance(progress, dict) or progress.get("enabled") is not True:
        return None
    try:
        return GitHubProgress(
            repository=str(job["repository"]),
            pr_number=int(job["pr_number"]),
            delivery=str(job["delivery"]),
            operation=str(job["operation"]),
            command_comment_id=int(job["command_comment_id"]),
            heartbeat_seconds=int(progress.get("heartbeat_seconds", 900)),
            command_runner=command_runner,
            logger=logger,
        )
    except (KeyError, TypeError, ValueError):
        return None

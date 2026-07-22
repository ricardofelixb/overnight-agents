from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from github_progress import GitHubProgress, progress_from_job


class FakeGitHub:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.comment_id: int | None = None
        self.bodies: list[str] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        endpoint = next(
            part for part in command if part.startswith("repos/")
        )
        if endpoint.endswith("/reactions"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if endpoint.endswith("/comments") and "GET" in command:
            output = f"{self.comment_id}\n" if self.comment_id else ""
            return subprocess.CompletedProcess(command, 0, output, "")
        body = next(
            (part.removeprefix("body=") for part in command if part.startswith("body=")),
            "",
        )
        if body:
            self.bodies.append(body)
        if endpoint.endswith("/comments") and "POST" in command:
            self.comment_id = 404
            return subprocess.CompletedProcess(command, 0, "404\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class GitHubProgressTests(unittest.TestCase):
    def progress(self, github: FakeGitHub) -> GitHubProgress:
        return GitHubProgress(
            repository="trusted/example",
            pr_number=17,
            delivery="delivery-1",
            operation="review",
            command_comment_id=991,
            heartbeat_seconds=900,
            command_runner=github.run,
        )

    def test_acknowledges_and_updates_one_idempotent_comment(self) -> None:
        github = FakeGitHub()
        first = self.progress(github)
        first.acknowledge_queued()
        first.phase("Reviewing with specialists", "Three bounded passes are running.")

        recovered = self.progress(github)
        recovered.phase("Verifying repairs", "Independent verification is running.")

        created = [command for command in github.commands if "POST" in command and command[-1] == ".id"]
        patched = [command for command in github.commands if "PATCH" in command]
        reactions = [command for command in github.commands if command[-1] == "--silent" and "/reactions" in " ".join(command)]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(patched), 2)
        self.assertEqual(len(reactions), 1)
        self.assertTrue(
            all("<!-- overnight-agents-progress:delivery-1 -->" in body for body in github.bodies)
        )
        self.assertIn("Verifying repairs", github.bodies[-1])

    def test_progress_failures_never_escape(self) -> None:
        messages: list[str] = []

        def fail(_command: list[str]) -> subprocess.CompletedProcess[str]:
            raise RuntimeError("network unavailable")

        progress = GitHubProgress(
            repository="trusted/example",
            pr_number=17,
            delivery="delivery-1",
            operation="simplify",
            command_comment_id=991,
            command_runner=fail,
            logger=messages.append,
        )
        progress.acknowledge_queued()
        progress.phase("Simplifying", "Work continues.")
        progress.finish("failed", "Worker failed", "Inspect local logs.")
        self.assertTrue(messages)

    def test_job_factory_is_backward_compatible(self) -> None:
        self.assertIsNone(progress_from_job({"version": 1}))
        progress = progress_from_job(
            {
                "version": 2,
                "repository": "trusted/example",
                "pr_number": 17,
                "delivery": "delivery-1",
                "operation": "review",
                "command_comment_id": 991,
                "progress": {"enabled": True, "heartbeat_seconds": 900},
            }
        )
        self.assertIsNotNone(progress)


if __name__ == "__main__":
    unittest.main()

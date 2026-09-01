from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.completion_reporter import completion_payload, report_completion


class SuccessfulResponse:
    status = 204

    def __enter__(self) -> "SuccessfulResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status


class CompletionReporterTests(unittest.TestCase):
    def private_env(self, root: Path, content: str) -> Path:
        path = root / ".env"
        path.write_text(content)
        path.chmod(0o600)
        return path

    def test_success_posts_stable_payload_and_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = self.private_env(
                Path(temporary),
                "GROK_BOT_WEBHOOK_URL=https://bot.example/jobs\n"
                "GROK_BOT_WEBHOOK_KEY=sender-secret\n",
            )
            with mock.patch(
                "automation.completion_reporter.urllib.request.urlopen",
                return_value=SuccessfulResponse(),
            ) as opener:
                delivered = report_completion(
                    job="code-maintainer",
                    repo="owner/project",
                    status="success",
                    summary="Audit required no changes.",
                    pr_url="",
                    commit="",
                    env_path=env,
                )

            self.assertTrue(delivered)
            request = opener.call_args.args[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(request.get_header("Authorization"), "Bearer sender-secret")
            self.assertEqual(
                json.loads(request.data),
                {
                    "job": "code-maintainer",
                    "repo": "owner/project",
                    "status": "success",
                    "summary": "Audit required no changes.",
                    "pr_url": "",
                    "commit": "",
                },
            )

    def test_missing_configuration_is_a_no_op(self) -> None:
        messages: list[str] = []
        with mock.patch("automation.completion_reporter.urllib.request.urlopen") as opener:
            delivered = report_completion(
                job="pr-reviewer",
                repo="owner/project",
                status="success",
                summary="Clean.",
                environment={},
                logger=messages.append,
            )
        self.assertFalse(delivered)
        opener.assert_not_called()
        self.assertTrue(any("unset" in message for message in messages))

    def test_post_failure_does_not_change_the_job_result(self) -> None:
        underlying_result = 0
        messages: list[str] = []
        with mock.patch(
            "automation.completion_reporter.urllib.request.urlopen",
            side_effect=OSError("network down"),
        ):
            delivered = report_completion(
                job="pr-simplifier",
                repo="owner/project",
                status="success",
                summary="No simplification needed.",
                environment={
                    "GROK_BOT_WEBHOOK_URL": "https://bot.example/jobs",
                    "GROK_BOT_WEBHOOK_KEY": "sender-secret",
                },
                logger=messages.append,
            )
        self.assertEqual(underlying_result, 0)
        self.assertFalse(delivered)
        self.assertTrue(any("unchanged" in message for message in messages))

    def test_non_private_environment_file_is_rejected_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = self.private_env(
                Path(temporary),
                "GROK_BOT_WEBHOOK_URL=https://bot.example/jobs\n"
                "GROK_BOT_WEBHOOK_KEY=sender-secret\n",
            )
            env.chmod(0o644)
            with mock.patch(
                "automation.completion_reporter.urllib.request.urlopen"
            ) as opener:
                delivered = report_completion(
                    job="code-maintainer",
                    repo="owner/project",
                    status="failure",
                    summary="Blocked.",
                    env_path=env,
                    logger=lambda _: None,
                )
        self.assertFalse(delivered)
        opener.assert_not_called()

    def test_payload_rejects_noncanonical_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "success or failure"):
            completion_payload(
                job="code-maintainer",
                repo="owner/project",
                status="blocked",
                summary="Blocked.",
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from configure_webhook import configure, ensure_env
from webhook import DeliveryQueue, WebhookApplication, signature_is_valid


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}

    def enqueue(self, delivery: str, value: dict) -> bool:
        if delivery in self.jobs:
            return False
        self.jobs[delivery] = value
        return True


class WebhookTests(unittest.TestCase):
    def application(self) -> tuple[WebhookApplication, FakeQueue]:
        queue = FakeQueue()
        application = WebhookApplication(
            secret="test-secret",
            endpoint_path="/github-webhook",
            projects=[{
                "name": "example",
                "repository": "trusted/example",
                "enabled": True,
                "excluded_authors": ["dependabot[bot]", "app/dependabot"],
            }],
            queue=queue,  # type: ignore[arg-type]
        )
        return application, queue

    def pull_request_payload(self, author: str = "trusted") -> dict:
        return {
            "action": "opened",
            "number": 17,
            "repository": {"full_name": "trusted/example"},
            "pull_request": {"draft": False, "user": {"login": author}},
        }

    def test_signature_validation_is_exact(self) -> None:
        body = b'{"zen":"safe"}'
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(signature_is_valid("secret", body, signature))
        self.assertFalse(signature_is_valid("secret", body + b"x", signature))
        self.assertFalse(signature_is_valid("", body, signature))

    def test_human_pull_request_is_queued_once(self) -> None:
        application, queue = self.application()
        status, body = application.handle("pull_request", "delivery-1", self.pull_request_payload())
        self.assertEqual((status, body["status"]), (202, "queued"))
        self.assertEqual(queue.jobs["delivery-1"]["pr_number"], 17)
        _, duplicate = application.handle("pull_request", "delivery-1", self.pull_request_payload())
        self.assertEqual(duplicate["status"], "duplicate")

    def test_dependabot_and_drafts_are_ignored(self) -> None:
        application, queue = self.application()
        _, bot = application.handle(
            "pull_request", "delivery-bot", self.pull_request_payload("dependabot[bot]")
        )
        draft_payload = self.pull_request_payload()
        draft_payload["pull_request"]["draft"] = True
        _, draft = application.handle("pull_request", "delivery-draft", draft_payload)
        self.assertEqual(bot, {"status": "ignored", "reason": "author"})
        self.assertEqual(draft, {"status": "ignored", "reason": "draft"})
        self.assertEqual(queue.jobs, {})

    def test_failed_check_suite_forces_same_head_review(self) -> None:
        application, queue = self.application()
        payload = {
            "action": "completed",
            "repository": {"full_name": "trusted/example"},
            "check_suite": {"conclusion": "failure", "pull_requests": [{"number": 17}]},
        }
        status, body = application.handle("check_suite", "delivery-ci", payload)
        self.assertEqual((status, body), (202, {"status": "queued", "count": 1}))
        self.assertTrue(queue.jobs["delivery-ci-17"]["force"])
        self.assertEqual(queue.jobs["delivery-ci-17"]["action"], "check_suite_failure")

    def test_human_review_feedback_forces_updated_context_review(self) -> None:
        application, queue = self.application()
        payload = {
            "action": "submitted",
            "repository": {"full_name": "trusted/example"},
            "pull_request": {"number": 17, "user": {"login": "trusted"}},
        }
        status, body = application.handle("pull_request_review", "delivery-review", payload)
        self.assertEqual((status, body["status"]), (202, "queued"))
        self.assertTrue(queue.jobs["delivery-review"]["force"])
        self.assertEqual(queue.jobs["delivery-review"]["action"], "pull_request_review:submitted")

    def test_interrupted_delivery_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            processing = root / "webhook-queue" / "processing"
            processing.mkdir(parents=True)
            (processing / "delivery.json").write_text(json.dumps({"delivery": "delivery"}))
            queue = DeliveryQueue(root, ROOT / "review.py", ROOT / "config.json", True, root / "worker.log")
            self.assertTrue((queue.pending / "delivery.json").exists())

    def test_env_setup_preserves_existing_values_and_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / ".env"
            env.write_text("TELEGRAM_CHAT_ID=123\n")
            env.chmod(0o600)
            values = ensure_env(env, "https://mini.example.ts.net:8443/github-webhook")
            text = env.read_text()
            self.assertIn("TELEGRAM_CHAT_ID=123", text)
            self.assertIn("GITHUB_WEBHOOK_SECRET=", text)
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(values["GITHUB_WEBHOOK_SECRET"], "")

    def test_github_hook_uses_pull_request_and_failed_check_events(self) -> None:
        with mock.patch("configure_webhook.gh", return_value=[]), mock.patch(
            "configure_webhook.gh_with_payload",
            return_value={
                "id": 42,
                "active": True,
                "events": [
                    "pull_request",
                    "pull_request_review",
                    "pull_request_review_comment",
                    "check_suite",
                ],
                "config": {"url": "https://mini.example.ts.net:8443/github-webhook"},
            },
        ) as send:
            result = configure(
                "trusted/example",
                "https://mini.example.ts.net:8443/github-webhook",
                "private-secret",
            )
        payload = send.call_args.args[2]
        self.assertEqual(
            payload["events"],
            ["pull_request", "pull_request_review", "pull_request_review_comment", "check_suite"],
        )
        self.assertEqual(payload["config"]["secret"], "private-secret")
        self.assertEqual(result["action"], "created")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from telegram_notify import (
    NotificationFailure,
    deliver_notification,
    enqueue_notification,
    flush_pending,
    format_blocked_message,
    format_merge_message,
    load_credentials,
)


def event() -> dict:
    return {
        "version": 1,
        "type": "pr_merged",
        "created_at": "2026-07-16T12:00:00+00:00",
        "project": "exac",
        "pr_number": 123,
        "title": "Simplify authenticated dashboard state",
        "url": "https://github.com/example/exac/pull/123",
        "base_branch": "master",
        "head_sha": "a" * 40,
        "changed_files": ["src/components/Dashboard.tsx", "convex/users.ts"],
        "domains": ["react", "workos", "convex"],
        "repair_count": 1,
    }


def blocked_event() -> dict:
    return {
        "version": 1,
        "type": "pr_blocked",
        "created_at": "2026-07-16T12:00:00+00:00",
        "project": "exac",
        "pr_number": 108,
        "title": "Simplify caja",
        "url": "https://github.com/example/exac/pull/108",
        "head_sha": "b" * 40,
        "repairs_applied": True,
        "blockers": ["receipt-history scope requires a product decision"],
        "findings": [{"id": "scope", "severity": "P1", "title": "Receipt scope is ignored"}],
    }


class SuccessfulResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self) -> str:
        return "https://api.telegram.org/success"

    def read(self, limit: int) -> bytes:
        del limit
        return b'{"ok": true}'


class TelegramNotificationTests(unittest.TestCase):
    def credentials(self, root: Path) -> Path:
        path = root / ".env"
        path.write_text("TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_CHAT_ID=12345\n")
        path.chmod(0o600)
        return path

    def test_message_contains_deployment_and_domain_sanity_checks(self) -> None:
        message = format_merge_message(event())
        self.assertIn("squash-merged into master", message)
        self.assertIn("vercel --prod", message)
        self.assertIn("organization context", message)
        self.assertIn("Convex reads/writes", message)
        self.assertIn("loading, empty, error", message)

    def test_delivery_archives_a_durable_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_path = self.credentials(root)
            queued = enqueue_notification(root, event())
            self.assertTrue(queued.is_file())
            with mock.patch("telegram_notify.urllib.request.urlopen", return_value=SuccessfulResponse()):
                deliver_notification(queued, env_path, root)
            self.assertFalse(queued.exists())
            self.assertTrue((root / "notification-outbox" / "sent" / queued.name).is_file())

    def test_blocker_message_is_actionable_and_deduplicated_after_delivery(self) -> None:
        message = format_blocked_message(blocked_event())
        self.assertIn("requires a decision", message)
        self.assertIn("P1: Receipt scope is ignored", message)
        self.assertIn("independent repairs were pushed", message)
        self.assertIn("No merge", message)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_path = self.credentials(root)
            queued = enqueue_notification(root, blocked_event())
            self.assertIsNotNone(queued)
            assert queued is not None
            with mock.patch("telegram_notify.urllib.request.urlopen", return_value=SuccessfulResponse()):
                deliver_notification(queued, env_path, root)
            self.assertIsNone(enqueue_notification(root, blocked_event()))

    def test_failed_delivery_remains_pending_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_path = self.credentials(root)
            queued = enqueue_notification(root, event())
            with mock.patch(
                "telegram_notify.urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ):
                delivered, failed = flush_pending(root, env_path)
            self.assertEqual((delivered, failed), (0, 1))
            self.assertTrue(queued.is_file())

    def test_environment_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_path = self.credentials(root)
            os.chmod(env_path, 0o644)
            with self.assertRaises(NotificationFailure):
                load_credentials(env_path)


if __name__ == "__main__":
    unittest.main()

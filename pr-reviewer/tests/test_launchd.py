from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from install_launchd import definitions


class LaunchdTests(unittest.TestCase):
    def test_schedule_definitions_are_fail_closed_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = definitions(Path(temporary))
        notification_retry = values["com.overnight-agents.pr-reviewer"]
        webhook = values["com.overnight-agents.pr-reviewer-webhook"]
        refresh = values["com.overnight-agents.pr-reviewer-skills"]
        self.assertEqual(notification_retry["StartInterval"], 1800)
        self.assertNotIn("--apply", notification_retry["ProgramArguments"])
        self.assertEqual(refresh["StartCalendarInterval"], {"Weekday": 0, "Hour": 3, "Minute": 15})
        self.assertTrue(refresh["ProgramArguments"][1].endswith("refresh_context.py"))
        self.assertIn("--config", refresh["ProgramArguments"])
        self.assertNotIn("--promote", refresh["ProgramArguments"])
        self.assertNotIn("RunAtLoad", notification_retry)
        self.assertTrue(webhook["KeepAlive"])
        self.assertTrue(webhook["RunAtLoad"])
        self.assertIn("--apply", webhook["ProgramArguments"])
        self.assertIn("--env", webhook["ProgramArguments"])
        self.assertTrue(webhook["ProgramArguments"][1].endswith("webhook.py"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("install_launchd.py")
SPEC = importlib.util.spec_from_file_location("simplifier_install_launchd", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SimplifierLaunchdTests(unittest.TestCase):
    def test_agent_run_uses_and_cleans_an_isolated_local_convex_deployment(self) -> None:
        script = Path(__file__).with_name("simplify.sh").read_text()
        self.assertIn(
            'scripts/setup-worktree.sh --convex-mode local',
            script,
        )
        self.assertIn('trap cleanup_agent_convex EXIT', script)
        self.assertIn('scripts/cleanup-worktree.sh', script)

    def test_daily_cron_schedule_becomes_exact_calendar_intervals(self) -> None:
        self.assertEqual(
            MODULE.calendar_intervals("0 1,7,13,19 * * *"),
            [
                {"Hour": 1, "Minute": 0},
                {"Hour": 7, "Minute": 0},
                {"Hour": 13, "Minute": 0},
                {"Hour": 19, "Minute": 0},
            ],
        )

    def test_unsupported_schedule_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "daily minute/hour"):
            MODULE.calendar_intervals("0 1 * * 1")
        with self.assertRaisesRegex(ValueError, "comma-separated"):
            MODULE.calendar_intervals("0 */6 * * *")

    def test_definition_has_no_run_at_load_and_uses_shared_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = MODULE.definition(root, "0 1,7,13,19 * * *")
        self.assertEqual(value["Label"], MODULE.LABEL)
        self.assertEqual(value["ProgramArguments"], ["/bin/bash", str(root / "simplify.sh")])
        self.assertEqual(len(value["StartCalendarInterval"]), 4)
        self.assertNotIn("RunAtLoad", value)
        self.assertEqual(value["ThrottleInterval"], 60)


if __name__ == "__main__":
    unittest.main()

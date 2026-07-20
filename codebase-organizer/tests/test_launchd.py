from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "install_launchd.py"
SPEC = importlib.util.spec_from_file_location("organizer_install_launchd", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OrganizerLaunchdTests(unittest.TestCase):
    def test_schedule_becomes_exact_calendar_intervals(self) -> None:
        self.assertEqual(
            MODULE.calendar_intervals("30 2,8,14,20 * * *"),
            [
                {"Hour": 2, "Minute": 30},
                {"Hour": 8, "Minute": 30},
                {"Hour": 14, "Minute": 30},
                {"Hour": 20, "Minute": 30},
            ],
        )

    def test_definition_has_no_run_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = MODULE.definition(root, "30 2 * * *")
        self.assertEqual(value["Label"], MODULE.LABEL)
        self.assertEqual(
            value["ProgramArguments"],
            ["/usr/bin/python3", str(root / "organize.py"), "--apply"],
        )
        self.assertNotIn("RunAtLoad", value)

    def test_reads_json_schedule_and_rejects_interval_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"schedule": "30 2 * * *"}))
            self.assertEqual(MODULE.configured_schedule(path), "30 2 * * *")
        with self.assertRaisesRegex(ValueError, "comma-separated"):
            MODULE.calendar_intervals("0 */6 * * *")


if __name__ == "__main__":
    unittest.main()

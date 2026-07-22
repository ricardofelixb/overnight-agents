from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "install_launchd.py"
SPEC = importlib.util.spec_from_file_location("simplifier_install_launchd", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SimplifierLaunchdTests(unittest.TestCase):
    def test_daily_schedule_becomes_exact_calendar_intervals(self) -> None:
        self.assertEqual(
            MODULE.calendar_intervals("0 1,7,13,19 * * *"),
            [
                {"Hour": 1, "Minute": 0},
                {"Hour": 7, "Minute": 0},
                {"Hour": 13, "Minute": 0},
                {"Hour": 19, "Minute": 0},
            ],
        )

    def test_definition_runs_the_python_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = MODULE.definition(root, "0 1,7,13,19 * * *")
        self.assertEqual(value["Label"], MODULE.LABEL)
        self.assertEqual(
            value["ProgramArguments"],
            ["/usr/bin/python3", str(root / "controller.py"), "--apply"],
        )
        self.assertNotIn("RunAtLoad", value)

    def test_reads_the_standard_json_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"schedule": "0 1 * * *"}))
            self.assertEqual(MODULE.configured_schedule(path), "0 1 * * *")


if __name__ == "__main__":
    unittest.main()

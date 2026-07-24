from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "install_launchd.py"
SPEC = importlib.util.spec_from_file_location("maintainer_install_launchd", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MaintainerLaunchdTests(unittest.TestCase):
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

    def test_definition_runs_the_maintainer_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = MODULE.definition(root, "0 1,7,13,19 * * *")
        self.assertEqual(value["Label"], "com.overnight-agents.code-maintainer")
        self.assertEqual(
            value["ProgramArguments"],
            ["/usr/bin/python3", str(root / "controller.py"), "--apply"],
        )
        self.assertIn("/usr/sbin", value["EnvironmentVariables"]["PATH"].split(":"))
        self.assertNotIn("RunAtLoad", value)

    def test_reads_the_standard_json_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"schedule": "0 1 * * *"}))
            self.assertEqual(MODULE.configured_schedule(path), "0 1 * * *")

    def test_install_writes_only_the_maintainer_launch_agent(self) -> None:
        with (
            mock.patch.object(MODULE.sys, "platform", "darwin"),
            mock.patch.object(MODULE.sys, "argv", ["install_launchd.py"]),
            mock.patch.object(
                MODULE, "configured_schedule", return_value="0 1,7,13,19 * * *"
            ),
            mock.patch.object(
                MODULE.launchd, "install", return_value="installed"
            ) as install,
        ):
            self.assertEqual(MODULE.main(), 0)
        self.assertEqual(
            [call.args[0] for call in install.call_args_list],
            [MODULE.LABEL],
        )


if __name__ == "__main__":
    unittest.main()

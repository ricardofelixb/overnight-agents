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
            MODULE.calendar_intervals("0 13,18 * * *"),
            [
                {"Hour": 13, "Minute": 0},
                {"Hour": 18, "Minute": 0},
            ],
        )

    def test_definition_runs_the_named_project_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = MODULE.definition(root, "exac", "0 13 * * *")
        self.assertEqual(value["Label"], "com.overnight-agents.code-maintainer.exac")
        self.assertEqual(
            value["ProgramArguments"],
            [
                "/usr/bin/python3",
                str(root / "controller.py"),
                "--project",
                "exac",
                "--apply",
            ],
        )
        self.assertEqual(
            value["StartCalendarInterval"],
            [{"Hour": 13, "Minute": 0}],
        )
        self.assertNotIn("RunAtLoad", value)

    def test_reads_enabled_project_schedules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "name": "exac",
                                "enabled": True,
                                "schedule": "0 13 * * *",
                            },
                            {
                                "name": "agents",
                                "enabled": True,
                                "schedule": "0 18 * * *",
                            },
                            {
                                "name": "adwooh",
                                "enabled": False,
                                "schedule": "0 3 * * *",
                            },
                        ]
                    }
                )
            )
            self.assertEqual(
                MODULE.enabled_project_jobs(json.loads(path.read_text())),
                [("exac", "0 13 * * *"), ("agents", "0 18 * * *")],
            )

    def test_install_writes_one_launch_agent_per_enabled_project(self) -> None:
        config = {
            "version": 3,
            "enabled": True,
            "context": {
                "skills_lock": "skills.json",
                "skill_release_root": "skills",
                "ai_files_root": "ai-files",
                "docs_catalog": "docs.json",
                "docs_refresh_script": "refresh.py",
                "docs_cache": "docs-cache",
            },
            "projects": [
                {
                    "name": "exac",
                    "enabled": True,
                    "schedule": "0 13 * * *",
                    "source_path": "/tmp/exac",
                    "repository": "owner/exac",
                    "base_branch": "master",
                    "environment_file": "/tmp/exac.env",
                    "validation_commands": [["true"]],
                },
                {
                    "name": "agents",
                    "enabled": True,
                    "schedule": "0 18 * * *",
                    "source_path": "/tmp/agents",
                    "repository": "owner/agents",
                    "base_branch": "main",
                    "environment_file": "/tmp/agents.env",
                    "validation_commands": [["true"]],
                },
            ],
        }
        installed: list[str] = []

        def fake_install(label: str, _definition: dict[str, object], *, uninstall: bool) -> str:
            installed.append(("removed" if uninstall else "installed") + " " + label)
            return installed[-1]

        with (
            mock.patch.object(MODULE.sys, "platform", "darwin"),
            mock.patch.object(MODULE.sys, "argv", ["install_launchd.py"]),
            mock.patch.object(MODULE, "existing_labels", return_value=[MODULE.LEGACY_LABEL]),
            mock.patch.object(
                MODULE, "SCRIPT_DIR", Path("/tmp/maintainer")
            ),
            mock.patch.object(
                Path, "read_text", return_value=json.dumps(config)
            ),
            mock.patch.object(MODULE.launchd, "install", side_effect=fake_install),
        ):
            self.assertEqual(MODULE.main(), 0)
        self.assertEqual(
            installed,
            [
                "removed com.overnight-agents.code-maintainer",
                "installed com.overnight-agents.code-maintainer.agents",
                "installed com.overnight-agents.code-maintainer.exac",
            ],
        )


if __name__ == "__main__":
    unittest.main()

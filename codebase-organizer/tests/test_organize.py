from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "organize.py"
SPEC = importlib.util.spec_from_file_location("codebase_organizer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OrganizerTests(unittest.TestCase):
    def valid_config(self) -> dict[str, object]:
        return {
            "enabled": True,
            "schedule": "30 2 * * *",
            "provider": "codex",
            "projects": [
                {
                    "name": "example",
                    "enabled": True,
                    "source_path": "/source",
                    "repository": "owner/example",
                    "base_branch": "main",
                    "environment_file": "/private/example.env.local",
                    "checklist_file": "/private/example.md",
                    "validation_commands": [["pnpm", "run", "validate"]],
                }
            ],
        }

    def test_selects_only_first_top_level_unchecked_item(self) -> None:
        text = """# Checklist

- [x] **done** — Finished
  - nested detail
- [ ] **sales** — Align sales
  - Backend: convex/receipts
  - Frontend: components/receipts
- [ ] **calendar** — Align calendar
"""
        item = MODULE.first_unchecked_item(text)
        self.assertIsNotNone(item)
        assert item
        self.assertEqual(item.item_id, "sales")
        self.assertIn("Frontend: components/receipts", item.block)
        self.assertNotIn("calendar", item.block)

    def test_exact_transition_accepts_one_marker_only(self) -> None:
        original = "- [ ] **sales** — Align sales\n  - Keep behavior\n"
        item = MODULE.first_unchecked_item(original)
        assert item
        completed = MODULE.completed_checklist_text(original, item)
        MODULE.require_exact_checklist_transition(original, completed, item)
        with self.assertRaisesRegex(MODULE.OrganizerFailure, "exactly"):
            MODULE.require_exact_checklist_transition(
                original, completed + "extra\n", item
            )

    def test_configuration_requires_boolean_switches_and_array_commands(self) -> None:
        config = self.valid_config()
        MODULE.validate_config(config)
        config["enabled"] = "true"
        with self.assertRaisesRegex(MODULE.OrganizerFailure, "boolean"):
            MODULE.validate_config(config)

    def test_round_robin_skips_disabled_projects(self) -> None:
        config = self.valid_config()
        config["projects"] = [
            {**config["projects"][0], "name": "disabled", "enabled": False},
            {**config["projects"][0], "name": "enabled", "enabled": True},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "rotation"
            selected = MODULE.select_project(config, None, state)
        self.assertEqual(selected["name"], "enabled")

    def test_sensitive_paths_are_rejected(self) -> None:
        self.assertTrue(MODULE.sensitive_staged_path(".env.local"))
        self.assertTrue(MODULE.sensitive_staged_path("certificates/key.pem"))
        self.assertFalse(MODULE.sensitive_staged_path("src/sales/model.ts"))

    def test_restore_item_marker_changes_only_requested_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checklist = Path(temporary) / "organization.md"
            checklist.write_text(
                "- [x] **sales** — Sales\n- [x] **calendar** — Calendar\n"
            )
            MODULE.restore_item_marker(checklist, "sales")
            self.assertEqual(
                checklist.read_text(),
                "- [ ] **sales** — Sales\n- [x] **calendar** — Calendar\n",
            )

    def test_load_config_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps([]))
            with self.assertRaisesRegex(MODULE.OrganizerFailure, "JSON object"):
                MODULE.load_config(path)


if __name__ == "__main__":
    unittest.main()

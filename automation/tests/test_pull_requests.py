from __future__ import annotations

import unittest

from automation.pull_requests import (
    manual_ui_checks,
    manual_ui_section,
    parse_manual_ui_checks,
)


class PullRequestDescriptionTests(unittest.TestCase):
    def test_parses_agent_supplied_checks(self) -> None:
        supplied, checks = parse_manual_ui_checks(
            'Summary\nMANUAL_UI_CHECKS_JSON: ["Open settings and confirm the dialog appears."]'
        )

        self.assertTrue(supplied)
        self.assertEqual(checks, ["Open settings and confirm the dialog appears."])

    def test_empty_agent_list_is_authoritative(self) -> None:
        checks = manual_ui_checks(
            "MANUAL_UI_CHECKS_JSON: []",
            ["src/components/settings.tsx"],
            "settings",
        )

        self.assertEqual(checks, [])

    def test_ui_path_gets_safe_fallback_when_field_is_missing(self) -> None:
        checks = manual_ui_checks("summary only", ["src/components/settings.tsx"], "settings")

        self.assertEqual(len(checks), 1)
        self.assertIn("settings", checks[0])

    def test_description_section_uses_checkboxes(self) -> None:
        section = manual_ui_section(["Open settings and confirm the dialog appears."])

        self.assertIn("## Manual UI verification", section)
        self.assertIn("- [ ] Open settings", section)


if __name__ == "__main__":
    unittest.main()

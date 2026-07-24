from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from reporting import (  # noqa: E402
    ReportFailure,
    maintenance_report_sections,
    parse_maintenance_report,
)


ROLES = (
    "reuse-simplification",
    "maintainability-organization",
    "efficiency-performance",
    "correctness-reliability",
    "security-hardening",
)


def report_output(**overrides: object) -> str:
    value: dict[str, object] = {
        "summary": "Simplified the calendar slice without changing contracts.",
        "role_outcomes": [
            {
                "role": role,
                "status": "changed" if role == "security-hardening" else "no-change",
                "summary": f"Reviewed {role} and reconciled its findings.",
            }
            for role in ROLES
        ],
        "changes": [
            {
                "role": "security-hardening",
                "summary": "Authorized calendar mutations before business-rule checks.",
            }
        ],
        "deferred": [
            {
                "role": "maintainability-organization",
                "summary": "Deferred utils.ts rename because no canonical target exists.",
            }
        ],
        "rejected": [],
        "validation": ["pnpm run validate:worktree passed."],
        "verifier": "PASS — no contract or scope issues.",
    }
    value.update(overrides)
    return f"Summary\nMAINTENANCE_REPORT_JSON: {json.dumps(value)}"


class MaintenanceReportingTests(unittest.TestCase):
    def test_parses_and_renders_every_report_section(self) -> None:
        report = parse_maintenance_report(report_output(), ROLES)
        body = maintenance_report_sections(report)

        self.assertIn("## Specialist outcomes", body)
        self.assertIn("**Maintainability organization — No change:**", body)
        self.assertIn("## Changes made", body)
        self.assertIn("Authorized calendar mutations", body)
        self.assertIn("## Deferred findings", body)
        self.assertIn("no canonical target exists", body)
        self.assertIn("## Rejected findings\n- None.", body)
        self.assertIn("pnpm run validate:worktree passed", body)
        self.assertIn("**Independent verifier:** PASS", body)

    def test_rejects_a_missing_selected_role(self) -> None:
        outcomes = [
            {
                "role": role,
                "status": "no-change",
                "summary": "No actionable finding.",
            }
            for role in ROLES[:-1]
        ]

        with self.assertRaisesRegex(ReportFailure, "missing: security-hardening"):
            parse_maintenance_report(
                report_output(role_outcomes=outcomes),
                ROLES,
            )

    def test_rejects_missing_change_details_for_a_changed_tree(self) -> None:
        with self.assertRaisesRegex(
            ReportFailure, "at least one adopted change"
        ):
            parse_maintenance_report(report_output(changes=[]), ROLES)

    def test_rejects_multiple_structured_report_fields(self) -> None:
        output = report_output()

        with self.assertRaisesRegex(ReportFailure, "exactly one"):
            parse_maintenance_report(f"{output}\n{output}", ROLES)


if __name__ == "__main__":
    unittest.main()

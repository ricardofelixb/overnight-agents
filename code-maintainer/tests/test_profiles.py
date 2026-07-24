from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from profiles import ROLE_SET, ProfileFailure, load_project_profile, load_slices


class ProjectProfileTests(unittest.TestCase):
    def test_exac_routes_specialist_context_and_covers_perpetual_slices(self) -> None:
        skill_root = Path(__file__).resolve().parent.parent / "skills/code-maintainer"
        profile = load_project_profile(skill_root, "exac")
        self.assertGreaterEqual(len(profile.slices), 35)
        self.assertEqual(set(profile.role_context), ROLE_SET)
        self.assertTrue(
            any(path.name == "canonical-structure.md"
                for path in profile.role_context["maintainability-organization"])
        )
        self.assertFalse(
            any(path.name == "canonical-structure.md"
                for path in profile.role_context["efficiency-performance"])
        )
        for role in (
            "maintainability-organization",
            "efficiency-performance",
            "correctness-reliability",
            "security-hardening",
        ):
            self.assertTrue(
                any(
                    path.name == "workos-boundaries.md"
                    for path in profile.role_context[role]
                )
            )
        self.assertTrue(all(set(item.roles) == ROLE_SET for item in profile.slices))
        self.assertIn("calendar", {item.identifier for item in profile.slices})
        workos_slices = {
            item.identifier: item
            for item in profile.slices
            if item.identifier in {
                "organizations-settings",
                "members-access",
                "auth-users-profile",
            }
        }
        self.assertEqual(len(workos_slices), 3)
        self.assertTrue(
            all("workos" in item.guidance_domains for item in workos_slices.values())
        )

    def test_slice_registry_rejects_escaping_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "slices.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {
                            "roles": sorted(ROLE_SET),
                            "guidance_domains": [],
                        },
                        "slices": [
                            {
                                "id": "unsafe",
                                "title": "Unsafe",
                                "selectors": ["../outside"],
                            }
                        ],
                    }
                )
            )
            with self.assertRaisesRegex(ProfileFailure, "unsafe selector"):
                load_slices(path)


if __name__ == "__main__":
    unittest.main()

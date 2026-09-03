from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from profiles import (
    ROLE_SET,
    ProfileFailure,
    load_project_profile,
    load_slices,
    validate_profile_selectors,
)


class ProjectProfileTests(unittest.TestCase):
    def test_exac_routes_specialist_context_and_covers_perpetual_slices(self) -> None:
        skill_root = Path(__file__).resolve().parent.parent / "skills/code-maintainer"
        profile = load_project_profile(skill_root, "exac")
        self.assertGreaterEqual(len(profile.slices), 35)
        self.assertEqual(set(profile.role_context), ROLE_SET)
        self.assertFalse(
            any(path.name == "canonical-structure.md"
                for path in profile.role_context["efficiency-performance"])
        )
        for role in (
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
        self.assertIn("collection-agents", {item.identifier for item in profile.slices})
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

    def test_agents_routes_core_and_company_slices_without_provider_domains(self) -> None:
        skill_root = Path(__file__).resolve().parent.parent / "skills/code-maintainer"
        profile = load_project_profile(skill_root, "agents")
        identifiers = {item.identifier for item in profile.slices}
        self.assertGreaterEqual(len(profile.slices), 12)
        self.assertEqual(set(profile.role_context), ROLE_SET)
        self.assertTrue(
            {"core-sdk", "company-exac", "livekit-voice"} <= identifiers
        )
        self.assertTrue(all(set(item.roles) == ROLE_SET for item in profile.slices))
        self.assertTrue(
            all(item.guidance_domains == () for item in profile.slices)
        )
        self.assertFalse(
            any(
                path.name == "workos-boundaries.md"
                for paths in profile.role_context.values()
                for path in paths
            )
        )
        source = Path("/Users/ricardo/Projects/agents")
        if source.exists():
            tracked = subprocess.check_output(
                ["git", "-C", str(source), "ls-tree", "-r", "--name-only", "origin/main"],
                text=True,
            ).splitlines()
            with tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                for relative in tracked:
                    path = workspace / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("")
                validate_profile_selectors(profile, workspace)

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

    def test_selector_validation_reports_every_stale_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            (workspace / "source").mkdir()
            (workspace / "source/current.ts").write_text("")
            profile = load_project_profile(
                Path(__file__).resolve().parent.parent / "skills/code-maintainer",
                "exac",
            )
            item = profile.slices[0]
            profile = profile.__class__(
                name=profile.name,
                root=profile.root,
                manifest_path=profile.manifest_path,
                shared_context=profile.shared_context,
                role_context=profile.role_context,
                slices_path=profile.slices_path,
                slices=(
                    item.__class__(
                        identifier="example",
                        title="Example",
                        selectors=("source/current.ts", "source/missing.ts"),
                        search_terms=(),
                        roles=item.roles,
                        guidance_domains=(),
                    ),
                ),
            )

            with self.assertRaisesRegex(
                ProfileFailure,
                r"(?s)example: source/missing\.ts.*Update",
            ):
                validate_profile_selectors(profile, workspace)

    def test_selector_validation_supports_recursive_globs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            validator = workspace / "convex/domain/validators.ts"
            validator.parent.mkdir(parents=True)
            validator.write_text("")
            profile = load_project_profile(
                Path(__file__).resolve().parent.parent / "skills/code-maintainer",
                "exac",
            )
            item = profile.slices[0]
            profile = profile.__class__(
                name=profile.name,
                root=profile.root,
                manifest_path=profile.manifest_path,
                shared_context=profile.shared_context,
                role_context=profile.role_context,
                slices_path=profile.slices_path,
                slices=(
                    item.__class__(
                        identifier="schema",
                        title="Schema",
                        selectors=("convex/**/validators.ts",),
                        search_terms=(),
                        roles=item.roles,
                        guidance_domains=(),
                    ),
                ),
            )

            validate_profile_selectors(profile, workspace)


if __name__ == "__main__":
    unittest.main()

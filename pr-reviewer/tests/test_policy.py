from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy import detect_domains, evaluate_pr_eligibility, validate_config


SHA_A = "a" * 40
SHA_B = "b" * 40


class PolicyTests(unittest.TestCase):
    def project(self) -> dict:
        return {
            "repository": "trusted/example",
            "base_branch": "main",
            "allowed_head_patterns": ["code-simplify/*"],
            "allowed_authors": ["trusted"],
            "allow_forks": False,
        }

    def pull_request(self) -> dict:
        return {
            "state": "OPEN",
            "isDraft": False,
            "baseRefName": "main",
            "headRefName": "code-simplify/example",
            "author": {"login": "trusted"},
            "isCrossRepository": False,
            "headRepositoryOwner": {"login": "trusted"},
            "baseRefOid": SHA_A,
            "headRefOid": SHA_B,
        }

    def test_clean_pr_is_eligible(self) -> None:
        self.assertEqual(evaluate_pr_eligibility(self.pull_request(), self.project()), [])

    def test_all_same_repository_human_branches_can_be_enabled(self) -> None:
        project = self.project() | {
            "allowed_head_patterns": ["*"],
            "allowed_authors": [],
            "excluded_authors": ["dependabot[bot]", "app/dependabot"],
        }
        pr = self.pull_request() | {
            "headRefName": "feature/customer-import",
            "author": {"login": "collaborator"},
        }
        self.assertEqual(evaluate_pr_eligibility(pr, project), [])
        self.assertIn(
            "pull request author is excluded",
            evaluate_pr_eligibility(pr | {"author": {"login": "dependabot[bot]"}}, project),
        )

    def test_unsafe_pr_dimensions_fail_closed(self) -> None:
        cases = {
            "draft": {"isDraft": True},
            "fork": {"isCrossRepository": True},
            "author": {"author": {"login": "attacker"}},
            "branch": {"headRefName": "feature/untrusted"},
            "sha": {"headRefOid": "not-a-sha"},
        }
        for name, update in cases.items():
            with self.subTest(name=name):
                pr = self.pull_request() | update
                self.assertTrue(evaluate_pr_eligibility(pr, self.project()))

    def test_detects_provider_domains(self) -> None:
        domains = detect_domains(
            ["convex/users.ts", "src/components/Login.tsx"],
            'import { WorkOS } from "@workos-inc/node"',
        )
        self.assertEqual(domains, ["convex", "react", "workos"])

    def test_merge_mode_is_rejected(self) -> None:
        config = {
            "version": 1,
            "skill_path": "skill",
            "workspace_root": "workspaces",
            "state_root": "state",
            "docs_catalog": "docs.json",
            "skills_lock": "skills.json",
            "defaults": {"mode": "merge"},
            "projects": [{
                "name": "example",
                "source_path": "/tmp/example",
                "allowed_head_patterns": ["code-simplify/*"],
                "allowed_authors": ["trusted"],
                "validation_commands": [["true"]],
            }],
        }
        self.assertIn("example: mode must be observe or repair", validate_config(config, Path("x")))

    def test_validation_environment_rejects_credentials(self) -> None:
        config = {
            "version": 1,
            "skill_path": "skill",
            "workspace_root": "workspaces",
            "state_root": "state",
            "docs_catalog": "docs.json",
            "skills_lock": "skills.json",
            "telegram_env": ".env",
            "defaults": {"validation_environment": {"API_TOKEN": "secret"}},
            "projects": [{
                "name": "example",
                "source_path": "/tmp/example",
                "repository": "trusted/example",
                "base_branch": "main",
                "allowed_head_patterns": ["code-simplify/*"],
                "allowed_authors": ["trusted"],
                "validation_commands": [["true"]],
            }],
        }
        self.assertIn(
            "example: validation_environment contains an unsafe name or value",
            validate_config(config, Path("x")),
        )

    def test_validation_correction_cycles_are_bounded(self) -> None:
        config = {
            "version": 1,
            "skill_path": "skill",
            "workspace_root": "workspaces",
            "state_root": "state",
            "docs_catalog": "docs.json",
            "skills_lock": "skills.json",
            "telegram_env": ".env",
            "webhook_env": ".env",
            "defaults": {"validation_correction_cycles": 4},
            "projects": [{
                "name": "example",
                "source_path": "/tmp/example",
                "repository": "trusted/example",
                "base_branch": "main",
                "allowed_head_patterns": ["*"],
                "allowed_authors": [],
                "validation_commands": [["true"]],
            }],
        }
        self.assertIn(
            "example: validation_correction_cycles must be between 1 and 3",
            validate_config(config, Path("x")),
        )


if __name__ == "__main__":
    unittest.main()

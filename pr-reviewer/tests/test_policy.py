from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy import detect_domains, evaluate_merge_gate, evaluate_pr_eligibility, validate_config


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

    def test_merge_gate_rejects_stale_head_and_base(self) -> None:
        gate = {
            "mode": "merge",
            "eligible": True,
            "consensus_clean": True,
            "documentation_current": True,
            "validation_passed": True,
            "required_checks_passed": True,
            "mergeable": True,
            "merge_state_clean": True,
            "reviewed_head_sha": SHA_A,
            "current_head_sha": SHA_B,
            "reviewed_base_sha": SHA_A,
            "current_base_sha": SHA_B,
            "unresolved_blockers": False,
        }
        errors = evaluate_merge_gate(gate)
        self.assertIn("pull request head changed after review", errors)
        self.assertIn("pull request base changed after review", errors)

    def test_detects_provider_domains(self) -> None:
        domains = detect_domains(
            ["convex/users.ts", "src/components/Login.tsx"],
            'import { WorkOS } from "@workos-inc/node"',
        )
        self.assertEqual(domains, ["convex", "react", "workos"])

    def test_invalid_autofix_severity_is_rejected(self) -> None:
        config = {
            "version": 1,
            "skill_path": "skill",
            "workspace_root": "workspaces",
            "state_root": "state",
            "docs_catalog": "docs.json",
            "skills_lock": "skills.json",
            "defaults": {"auto_fix_severities": ["critical"]},
            "projects": [{
                "name": "example",
                "source_path": "/tmp/example",
                "allowed_head_patterns": ["code-simplify/*"],
                "allowed_authors": ["trusted"],
                "validation_commands": [["true"]],
            }],
        }
        self.assertIn("example: auto_fix_severities must contain valid severities", validate_config(config, Path("x")))


if __name__ == "__main__":
    unittest.main()

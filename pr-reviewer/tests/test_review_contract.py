from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "autonomous-pr-review" / "scripts" / "review_contract.py"
SPEC = importlib.util.spec_from_file_location("review_contract", SCRIPT)
assert SPEC and SPEC.loader
review_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_contract)


SHA_A = "a" * 40
SHA_B = "b" * 40
CHANGED = {"src/example.ts"}


def result(verdict: str = "clean", lens: str = "behavior-contracts") -> dict:
    value = {
        "schema_version": 1,
        "phase": "analysis",
        "lens": lens,
        "verdict": verdict,
        "reviewed_base_sha": SHA_A,
        "reviewed_head_sha": SHA_B,
        "behavioral_contracts": [{"contract": "returns the same value", "status": "preserved", "evidence": "caller test"}],
        "findings": [],
        "coverage": {
            "changed_files_reviewed": ["src/example.ts"],
            "changed_files_unreviewed": [],
        },
        "documentation": [],
        "blocking_reasons": [],
    }
    if verdict == "fixable":
        value["behavioral_contracts"][0]["status"] = "regressed"
        value["findings"] = [{
            "id": "example-regression",
            "severity": "P2",
            "start_line": 1,
            "end_line": 2,
            "file": "src/example.ts",
            "auto_fix_safe": True,
        }]
    if verdict == "blocked":
        value["blocking_reasons"] = ["missing generated types"]
    return value


class ReviewContractTests(unittest.TestCase):
    def validate(self, value: dict, **kwargs) -> list[str]:
        return review_contract.validate_review_result(
            value,
            expected_base=SHA_A,
            expected_head=SHA_B,
            expected_phase="analysis",
            changed_files=CHANGED,
            **kwargs,
        )

    def test_clean_fixture_is_valid(self) -> None:
        self.assertEqual(self.validate(result()), [])

    def test_unreviewed_file_prevents_clean(self) -> None:
        value = result()
        value["coverage"]["changed_files_reviewed"] = []
        value["coverage"]["changed_files_unreviewed"] = ["src/example.ts"]
        self.assertIn("clean verdict cannot contain unreviewed files", self.validate(value))

    def test_fixable_and_blocked_fixtures_are_valid(self) -> None:
        self.assertEqual(self.validate(result("fixable")), [])
        self.assertEqual(self.validate(result("blocked")), [])

    def test_finding_outside_diff_is_rejected(self) -> None:
        value = result("fixable")
        value["findings"][0]["file"] = "src/unchanged.ts"
        self.assertIn("finding example-regression: file is not changed", self.validate(value))

    def test_provider_evidence_must_match_controller_manifests(self) -> None:
        value = result()
        docs = {"domains": ["react"], "documents": [{
            "domain": "react", "url": "https://react.dev/reference/react", "final_url": "https://react.dev/reference/react"
        }]}
        skills = {"domains": {"react": [{"name": "react-best-practices", "revision": SHA_A}]}}
        errors = self.validate(value, docs_manifest=docs, skills_manifest=skills)
        self.assertIn("missing documentation evidence for react", errors)
        value["documentation"] = [{
            "provider": "react",
            "url": "https://evil.example/rules",
            "skill": "react-best-practices",
            "skill_revision": SHA_A,
        }]
        self.assertIn("unapproved documentation URL for react", self.validate(value, docs_manifest=docs, skills_manifest=skills))

    def test_consensus_requires_exact_distinct_lenses(self) -> None:
        first = result(lens="behavior-contracts")
        second = result(lens="systems-boundaries")
        self.assertEqual(review_contract.validate_consensus([first, second], {"behavior-contracts", "systems-boundaries"}), [])
        self.assertTrue(review_contract.validate_consensus([first, first], {"behavior-contracts", "systems-boundaries"}))


if __name__ == "__main__":
    unittest.main()

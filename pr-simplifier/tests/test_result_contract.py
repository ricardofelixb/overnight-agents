from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "skills" / "simplify-pr-implementation" / "scripts" / "result_contract.py"
SPEC = importlib.util.spec_from_file_location("simplify_result_contract", CONTRACT_PATH)
assert SPEC and SPEC.loader
result_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(result_contract)

SHA_A = "a" * 40
SHA_B = "b" * 40
PR_FILES = {"src/a.ts", "src/a.test.ts"}


def result(status: str = "clean") -> dict:
    value = {
        "schema_version": 1,
        "status": status,
        "reviewed_base_sha": SHA_A,
        "reviewed_head_sha": SHA_B,
        "summary": "No worthwhile simplification found.",
        "reviewed_files": sorted(PR_FILES),
        "specialists": [
            {"name": name, "summary": "No finding.", "inspected_files": sorted(PR_FILES), "accepted_improvement_ids": []}
            for name in sorted(result_contract.SPECIALISTS)
        ],
        "improvements": [],
        "changed_files": [],
        "tests_changed": [],
        "verification": {"performed": False, "verdict": "not_needed", "summary": "No edits.", "commands": []},
        "remaining_observations": [],
        "blocking_reasons": [],
    }
    if status in {"simplified", "simplified_blocked"}:
        value["summary"] = "Reused the existing helper."
        value["improvements"] = [{
            "id": "reuse-helper",
            "category": "reuse",
            "evidence": "The helper has identical semantics.",
            "benefit": "Removes duplicated normalization logic.",
            "why_behavior_is_preserved": "Both paths call the same tested implementation.",
            "files": ["src/a.ts", "src/a.test.ts"],
            "focused_checks": ["unit test"],
        }]
        value["changed_files"] = ["src/a.ts", "src/a.test.ts"]
        value["tests_changed"] = ["src/a.test.ts"]
        value["verification"] = {"performed": True, "verdict": "passed", "summary": "Verifier passed.", "commands": ["pnpm test"]}
        value["specialists"][0]["accepted_improvement_ids"] = ["reuse-helper"]
    if status in {"blocked", "simplified_blocked"}:
        value["blocking_reasons"] = ["Product behavior is ambiguous."]
    return value


class ResultContractTests(unittest.TestCase):
    def validate(self, value: dict, actual: set[str] | None = None) -> list[str]:
        if actual is None:
            actual = set(value["changed_files"])
        return result_contract.validate_result(
            value,
            expected_base=SHA_A,
            expected_head=SHA_B,
            pr_changed_files=PR_FILES,
            actual_changed_files=actual,
        )

    def test_all_status_shapes(self) -> None:
        for status in ("clean", "simplified", "blocked", "simplified_blocked"):
            with self.subTest(status=status):
                self.assertEqual(self.validate(result(status)), [])

    def test_requires_exact_shas_and_complete_review_surface(self) -> None:
        value = result()
        value["reviewed_head_sha"] = SHA_A
        value["reviewed_files"] = ["src/a.ts"]
        errors = self.validate(value)
        self.assertIn("head SHA mismatch", errors)
        self.assertIn("reviewed_files must include every PR changed file", errors)

    def test_requires_all_specialists_once(self) -> None:
        value = result()
        value["specialists"] = value["specialists"][:2]
        self.assertIn("all required specialist sub-agents must report", self.validate(value))
        value["specialists"].append(value["specialists"][0])
        self.assertIn("specialist names must be unique", self.validate(value))

    def test_reported_changes_must_match_worktree_and_improvements(self) -> None:
        value = result("simplified")
        self.assertIn(
            "changed_files must exactly match the working-tree diff",
            self.validate(value, {"src/a.ts"}),
        )
        value["improvements"][0]["files"] = ["src/a.ts"]
        self.assertIn("improvement files must exactly match changed_files", self.validate(value))

    def test_improvements_are_traceable_to_specialist_reports(self) -> None:
        value = result("simplified")
        value["specialists"][0]["accepted_improvement_ids"] = []
        self.assertIn("specialist accepted IDs must exactly match improvements", self.validate(value))

    def test_specialist_accepted_ids_remain_unique_without_schema_keyword(self) -> None:
        value = result("simplified")
        value["specialists"][0]["accepted_improvement_ids"] = ["reuse-helper", "reuse-helper"]
        self.assertTrue(
            any("accepted_improvement_ids must be unique" in error for error in self.validate(value))
        )

    def test_clean_and_blocked_cannot_hide_edits(self) -> None:
        for status in ("clean", "blocked"):
            value = result(status)
            value["changed_files"] = ["src/a.ts"]
            self.assertTrue(any("cannot" in error for error in self.validate(value)))

    def test_simplified_requires_fresh_passed_verification(self) -> None:
        value = result("simplified")
        value["verification"] = {"performed": False, "verdict": "not_needed", "summary": "Skipped.", "commands": []}
        errors = self.validate(value)
        self.assertIn("simplified result requires fresh verification", errors)
        self.assertIn("simplified result requires passed verification", errors)

    def test_rejects_escaping_or_non_normalized_paths(self) -> None:
        value = result()
        value["reviewed_files"].append("../secret")
        value["reviewed_files"].append("src//a.ts")
        errors = self.validate(value)
        self.assertTrue(any("invalid repository-relative path" in error for error in errors))

    def test_schema_constants_and_enums_have_types(self) -> None:
        schema = json.loads((ROOT / "skills" / "simplify-pr-implementation" / "references" / "orchestrator-result.schema.json").read_text())

        def visit(node: object, path: str = "$") -> list[str]:
            errors: list[str] = []
            if isinstance(node, dict):
                if ("const" in node or "enum" in node) and "type" not in node:
                    errors.append(path)
                for key, child in node.items():
                    errors.extend(visit(child, f"{path}.{key}"))
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    errors.extend(visit(child, f"{path}[{index}]"))
            return errors

        self.assertEqual(visit(schema), [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import json
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
SPECIALISTS = [
    {"name": "behavior-contracts", "scope": "behavior", "verdict": "clean", "summary": "preserved"},
    {"name": "security-provider", "scope": "security", "verdict": "clean", "summary": "preserved"},
    {"name": "hygiene-tests", "scope": "hygiene", "verdict": "clean", "summary": "preserved"},
]


def result(status: str = "clean") -> dict:
    value = {
        "schema_version": 2,
        "status": status,
        "reviewed_base_sha": SHA_A,
        "reviewed_head_sha": SHA_B,
        "summary": "review complete",
        "reviewed_files": ["src/example.ts"],
        "specialists": SPECIALISTS,
        "repairs": [],
        "changed_files": [],
        "tests_changed": [],
        "verification": {"performed": False, "verdict": "not_needed", "summary": "no edits"},
        "documentation": [],
        "manual_ui_checks": [],
        "remaining_observations": [],
        "blocking_reasons": [],
    }
    if status == "repaired":
        value["repairs"] = [{
            "id": "existing-defect",
            "provenance": "pre_existing",
            "category": "correctness",
            "title": "Fix existing defect",
            "evidence": "concrete failing input",
            "why_safe": "behavior is specified by a focused contract test",
            "files": ["src/example.ts"],
            "tests": ["src/example.ts"],
        }]
        value["changed_files"] = ["src/example.ts"]
        value["tests_changed"] = ["src/example.ts"]
        value["verification"] = {"performed": True, "verdict": "passed", "summary": "fresh verifier passed"}
    if status == "blocked":
        value["verification"] = {"performed": True, "verdict": "blocked", "summary": "ambiguous behavior"}
        value["blocking_reasons"] = ["product behavior is ambiguous"]
    if status == "repaired_blocked":
        repaired = result("repaired")
        value.update(repaired)
        value["status"] = "repaired_blocked"
        value["blocking_reasons"] = ["separate product behavior is ambiguous"]
    return value


class ReviewContractTests(unittest.TestCase):
    def manifests(self) -> tuple[dict, dict]:
        return {"domains": [], "documents": []}, {"domains": {}}

    def validate(self, value: dict, **kwargs) -> list[str]:
        docs, skills = self.manifests()
        return review_contract.validate_result(
            value,
            expected_base=SHA_A,
            expected_head=SHA_B,
            changed_files=CHANGED,
            docs_manifest=kwargs.get("docs_manifest", docs),
            skills_manifest=kwargs.get("skills_manifest", skills),
        )

    def test_schema_keywords_have_explicit_types(self) -> None:
        schema = json.loads(
            (ROOT / "skills" / "autonomous-pr-review" / "references" / "orchestrator-result.schema.json").read_text()
        )

        def visit(value: object, path: str = "$") -> list[str]:
            errors: list[str] = []
            if isinstance(value, dict):
                if ("const" in value or "enum" in value) and "type" not in value:
                    errors.append(path)
                for key, child in value.items():
                    errors.extend(visit(child, f"{path}.{key}"))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    errors.extend(visit(child, f"{path}[{index}]"))
            return errors

        self.assertEqual(visit(schema), [])

    def test_clean_repaired_and_blocked_results_are_valid(self) -> None:
        for status in ("clean", "repaired", "blocked", "repaired_blocked"):
            with self.subTest(status=status):
                self.assertEqual(self.validate(result(status)), [])

    def test_all_specialists_are_required(self) -> None:
        value = result()
        value["specialists"] = value["specialists"][:2]
        self.assertIn("all required specialist sub-agents must report", self.validate(value))

    def test_manual_ui_checks_are_bounded_and_unique(self) -> None:
        value = result()
        value["manual_ui_checks"] = [
            "Create an expense and confirm it appears in the table.",
            "Open the expense and confirm its supplier is displayed.",
        ]
        self.assertEqual(self.validate(value), [])
        value["manual_ui_checks"] = ["Repeat this check"] * 6
        errors = self.validate(value)
        self.assertIn("manual_ui_checks cannot exceed five items", errors)
        self.assertIn("manual_ui_checks must contain unique strings", errors)
        del value["manual_ui_checks"]
        self.assertIn("manual_ui_checks must be reported explicitly", self.validate(value))

    def test_reviewed_files_may_include_context_but_must_cover_the_diff(self) -> None:
        value = result()
        value["reviewed_files"].append("src/context.ts")
        self.assertEqual(self.validate(value), [])
        value["reviewed_files"] = ["src/context.ts"]
        self.assertIn("reviewed_files must include every PR changed file", self.validate(value))

    def test_repaired_result_accepts_pre_existing_provenance(self) -> None:
        self.assertEqual(result("repaired")["repairs"][0]["provenance"], "pre_existing")
        self.assertEqual(self.validate(result("repaired")), [])

    def test_repaired_result_requires_fresh_verification(self) -> None:
        value = result("repaired")
        value["verification"] = {"performed": False, "verdict": "not_needed", "summary": "skipped"}
        errors = self.validate(value)
        self.assertIn("repaired result requires fresh verification", errors)
        self.assertIn("repaired result requires passed verification", errors)

    def test_repaired_blocked_keeps_repairs_and_requires_a_blocker(self) -> None:
        value = result("repaired_blocked")
        self.assertEqual(self.validate(value), [])
        value["blocking_reasons"] = []
        self.assertIn("repaired_blocked result requires blocking reasons", self.validate(value))

    def test_provider_candidates_do_not_require_evidence(self) -> None:
        value = result()
        docs = {"domains": ["react"], "documents": [{
            "domain": "react",
            "url": "https://react.dev/reference/react",
            "final_url": "https://react.dev/reference/react",
            "retrieved_at": "2026-07-16T12:00:00+00:00",
        }]}
        skills = {"domains": {"react": [{"name": "react-best-practices", "revision": SHA_A}]}}
        self.assertEqual(self.validate(value, docs_manifest=docs, skills_manifest=skills), [])

    def test_selected_provider_evidence_must_match_catalogs(self) -> None:
        value = result()
        docs = {"domains": ["react"], "documents": [{
            "domain": "react",
            "url": "https://react.dev/reference/react",
            "final_url": "https://react.dev/reference/react",
            "retrieved_at": "2026-07-16T12:00:00+00:00",
        }]}
        skills = {"domains": {"react": [{"name": "react-best-practices", "revision": SHA_A}]}}
        value["documentation"] = [{
            "provider": "react",
            "url": "https://evil.example/rules",
            "retrieved_at": "2026-07-16T12:00:00+00:00",
            "skill": "react-best-practices",
            "skill_revision": SHA_A,
        }]
        self.assertIn(
            "unapproved documentation URL for react",
            self.validate(value, docs_manifest=docs, skills_manifest=skills),
        )
        value["documentation"][0]["url"] = "https://react.dev/reference/react"
        self.assertEqual(self.validate(value, docs_manifest=docs, skills_manifest=skills), [])


if __name__ == "__main__":
    unittest.main()

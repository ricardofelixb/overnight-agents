from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refresh_skills import RefreshFailure, audit_tree, discover_skills, promote, rollback, tree_hash, validate_repository_url


class SkillRefreshTests(unittest.TestCase):
    def test_only_official_allowlisted_repositories_are_accepted(self) -> None:
        validate_repository_url("https://github.com/get-convex/agent-skills.git")
        with self.assertRaises(RefreshFailure):
            validate_repository_url("https://github.com/attacker/agent-skills.git")
        with self.assertRaises(RefreshFailure):
            validate_repository_url("http://github.com/workos/skills.git")

    def test_discovers_and_hashes_a_valid_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: Test skill\n---\n# Example\n")
            audit_tree(skill)
            self.assertEqual(discover_skills(root), {"example": skill})
            first = tree_hash(skill)
            (skill / "reference.md").write_text("changed")
            self.assertNotEqual(first, tree_hash(skill))

    def test_escaping_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "example"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: Test skill\n---\n# Example\n")
            (skill / "escape").symlink_to(root.parent)
            with self.assertRaises(RefreshFailure):
                audit_tree(skill)

    def test_promotion_records_a_complete_atomic_rollback_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            global_root = root / "global"
            old = root / "old"
            new = root / "new"
            old.mkdir()
            new.mkdir()
            previous = {"name": "example", "path": str(old), "revision": "a" * 40, "sha256": "old"}
            entry = {"name": "example", "path": str(new), "revision": "b" * 40, "sha256": "new"}
            promote({"react": [entry]}, global_root, {"domains": {"react": [previous]}})
            self.assertEqual((global_root / "example").resolve(), new.resolve())
            self.assertEqual(entry["previous"], previous)
            restored = rollback({"domains": {"react": [entry]}}, global_root)
            self.assertEqual((global_root / "example").resolve(), old.resolve())
            self.assertEqual(restored["domains"]["react"], [previous])


if __name__ == "__main__":
    unittest.main()

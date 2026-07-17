from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refresh_ai_files import (
    AiFilesRefreshFailure,
    audit_and_hash,
    github_repository_from_origin,
    is_managed_ai_file,
    managed_snapshot_files,
    publish_snapshot,
)


class ConvexAiFilesRefreshTests(unittest.TestCase):
    def test_github_origin_must_match_a_canonical_repository(self) -> None:
        self.assertEqual(
            github_repository_from_origin("https://github.com/get-convex/convex.git"),
            "get-convex/convex",
        )
        self.assertEqual(
            github_repository_from_origin("git@github.com:get-convex/convex.git"),
            "get-convex/convex",
        )
        with self.assertRaisesRegex(AiFilesRefreshFailure, "not a GitHub"):
            github_repository_from_origin("https://example.test/attacker/repo.git")

    def test_managed_path_policy_is_narrow(self) -> None:
        allowed = [
            "AGENTS.md",
            "CLAUDE.md",
            "convex/_generated/ai/guidelines.md",
            ".agents/skills/convex/SKILL.md",
            ".claude/skills/convex-migration-helper/SKILL.md",
        ]
        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(is_managed_ai_file(path))
        for path in ("package.json", ".env.local", ".agents/skills/untrusted/SKILL.md"):
            with self.subTest(path=path):
                self.assertFalse(is_managed_ai_file(path))

    def test_publishes_hashed_snapshot_without_copying_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            guidelines = workspace / "convex" / "_generated" / "ai" / "guidelines.md"
            guidelines.parent.mkdir(parents=True)
            guidelines.write_text("# Guidance\n")
            skill = workspace / ".agents" / "skills" / "convex" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: convex\ndescription: Route\n---\n")
            (workspace / "package.json").write_text("{}\n")
            files = managed_snapshot_files(workspace)
            digest, manifest = audit_and_hash(workspace, files)
            self.assertIn("convex/_generated/ai/guidelines.md", manifest)
            manifest_path = publish_snapshot(
                workspace,
                root / "state",
                {
                    "name": "example",
                    "repository": "trusted/example",
                    "base_branch": "main",
                },
                "a" * 40,
            )
            self.assertTrue(manifest_path.is_file())
            release = root / "state" / "ai-files" / "example" / "releases" / digest
            self.assertTrue((release / ".agents" / "skills" / "convex" / "SKILL.md").is_file())
            self.assertFalse((release / "package.json").exists())

    def test_snapshot_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guidelines = root / "convex" / "_generated" / "ai" / "guidelines.md"
            guidelines.parent.mkdir(parents=True)
            target = root / "target"
            target.write_text("unsafe")
            guidelines.symlink_to(target)
            with self.assertRaisesRegex(AiFilesRefreshFailure, "symlink"):
                audit_and_hash(root, [guidelines])


if __name__ == "__main__":
    unittest.main()

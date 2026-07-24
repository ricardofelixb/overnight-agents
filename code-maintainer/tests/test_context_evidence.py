from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context_evidence import ContextFailure, validate_ai_files, validate_skill_lock
from context_evidence import validate_official_docs_manifest


class ContextEvidenceTests(unittest.TestCase):
    def config(self, root: Path) -> dict[str, object]:
        return {
            "_config_dir": str(root),
            "context": {
                "skills_lock": "skills.lock.json",
                "skill_release_root": "releases",
                "skill_max_age_days": 8,
                "ai_files_root": "ai-files",
                "ai_files_max_age_days": 8,
                "docs_cache": "docs-cache",
                "docs_max_age_hours": 24,
            },
        }

    def test_skill_lock_requires_current_hashed_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "releases/react/revision/skill"
            release.mkdir(parents=True)
            (release / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
            digest = hashlib.sha256()
            digest.update(b"SKILL.md\0")
            digest.update((release / "SKILL.md").read_bytes())
            digest.update(b"\0")
            lock = {
                "version": 1,
                "domains": {
                    "react": [
                        {
                            "name": "test",
                            "path": str(release),
                            "source": "https://example.test",
                            "revision": "revision",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "sha256": digest.hexdigest(),
                        }
                    ]
                },
            }
            (root / "skills.lock.json").write_text(json.dumps(lock))
            evidence = validate_skill_lock(self.config(root), ("react",))
            self.assertEqual(evidence["react"][0]["name"], "test")

    def test_convex_guidance_must_match_audited_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            guidance = workspace / "convex/_generated/ai/guidelines.md"
            guidance.parent.mkdir(parents=True)
            guidance.write_text("# Current\n")
            content = guidance.read_bytes()
            manifest = {
                "version": 1,
                "project": "exac",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "release_path": str(root / "release"),
                "base_sha": "a" * 40,
                "files": {
                    "convex/_generated/ai/guidelines.md": {
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                },
            }
            path = root / "ai-files/exac/manifest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(manifest))
            self.assertEqual(
                validate_ai_files(self.config(root), "exac", workspace)["project"],
                "exac",
            )
            guidance.write_text("# Stale\n")
            with self.assertRaisesRegex(ContextFailure, "differs"):
                validate_ai_files(self.config(root), "exac", workspace)

    def test_official_docs_require_fresh_hashed_content_in_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "docs-cache/react/reference.content"
            content.parent.mkdir(parents=True)
            content.write_text("# React\n")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "domains": ["react"],
                        "errors": [],
                        "documents": [
                            {
                                "domain": "react",
                                "content_path": str(content),
                                "retrieved_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                "sha256": hashlib.sha256(
                                    content.read_bytes()
                                ).hexdigest(),
                            }
                        ],
                    }
                )
            )
            evidence = validate_official_docs_manifest(
                self.config(root), manifest, ("react",)
            )
            self.assertEqual(evidence["domains"], ["react"])
            content.write_text("# Tampered\n")
            with self.assertRaisesRegex(ContextFailure, "hash mismatch"):
                validate_official_docs_manifest(
                    self.config(root), manifest, ("react",)
                )


if __name__ == "__main__":
    unittest.main()

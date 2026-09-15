from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context_evidence import (
    ContextFailure,
    ensure_provider_context,
    prepare_context_evidence,
    validate_ai_files,
    validate_official_docs_manifest,
    validate_skill_lock,
)


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
            release_guidance = (
                root
                / "ai-files/exac/releases/revision/convex/_generated/ai/guidelines.md"
            )
            release_guidance.parent.mkdir(parents=True)
            release_guidance.write_bytes(content)
            manifest = {
                "version": 1,
                "project": "exac",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "release_path": str(root / "ai-files/exac/releases/revision"),
                "base_sha": "a" * 40,
                "files": {
                    "convex/_generated/ai/guidelines.md": {
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                },
            }
            path = root / "ai-files/exac/manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest))
            self.assertEqual(
                validate_ai_files(self.config(root), "exac", workspace)["project"],
                "exac",
            )
            guidance.write_text("# Repository copy may be stale\n")
            validate_ai_files(self.config(root), "exac", workspace)
            release_guidance.write_text("# Tampered\n")
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

    def test_opted_out_projects_skip_provider_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"_config_dir": str(root), "context": None}
            path = prepare_context_evidence(
                config, "agents", (), root, stream=io.StringIO()
            )
            evidence = json.loads(path.read_text())
            self.assertEqual(evidence["project"], "agents")
            self.assertEqual(evidence["domains"], [])
            self.assertEqual(evidence["skills"], {})
            self.assertFalse(evidence["provider_context"])
            with self.assertRaisesRegex(ContextFailure, "opted out"):
                prepare_context_evidence(
                    config, "agents", ("react",), root, stream=io.StringIO()
                )

    def test_stale_provider_context_refreshes_once_before_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "_config_dir": str(root),
                "context": {"provider_refresh_script": "refresh_context.py"},
            }
            stream = io.StringIO()
            with (
                mock.patch(
                    "context_evidence.validate_skill_lock",
                    side_effect=[ContextFailure("audited skill is stale"), {}],
                ) as validate_skills,
                mock.patch(
                    "context_evidence.validate_ai_files_freshness"
                ) as validate_ai_files_freshness,
                mock.patch("context_evidence.runtime.run") as run,
            ):
                ensure_provider_context(
                    config, "exac", ("convex",), stream
                )

            self.assertEqual(validate_skills.call_count, 2)
            validate_ai_files_freshness.assert_called_once_with(config, "exac")
            run.assert_called_once_with(
                [
                    sys.executable,
                    str((root / "refresh_context.py").resolve()),
                    "--project",
                    "exac",
                ],
                stream=stream,
            )
            self.assertIn("requires refresh", stream.getvalue())


if __name__ == "__main__":
    unittest.main()

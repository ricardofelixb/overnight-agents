from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from review import ReviewFailure, tree_hash, validate_docs_manifest, validate_skill_lock


class ReviewSafetyTests(unittest.TestCase):
    def test_stale_skill_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            skill = state / "skill-releases" / "provider" / ("a" * 40) / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: example\ndescription: Example\n---\n")
            lock = state / "skills.lock.json"
            lock.write_text(json.dumps({
                "version": 1,
                "domains": {"react": [{
                    "name": "example",
                    "path": str(skill),
                    "updated_at": (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(),
                    "sha256": tree_hash(skill),
                }]},
            }))
            with self.assertRaisesRegex(ReviewFailure, "stale"):
                validate_skill_lock({"skills_lock": str(lock), "state_root": str(state), "skill_max_age_days": 8}, ["react"])

    def test_document_hash_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            content = state / "docs-cache" / "react" / "document.content"
            content.parent.mkdir(parents=True)
            content.write_bytes(b"official")
            manifest = state / "manifest.json"
            manifest.write_text(json.dumps({
                "version": 1,
                "domains": ["react"],
                "errors": [],
                "documents": [{
                    "domain": "react",
                    "url": "https://react.dev/reference/react",
                    "content_path": str(content),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(b"official").hexdigest(),
                }],
            }))
            config = {"state_root": str(state), "docs_max_age_hours": 24}
            validate_docs_manifest(config, manifest, ["react"])
            content.write_bytes(b"tampered")
            with self.assertRaisesRegex(ReviewFailure, "hash mismatch"):
                validate_docs_manifest(config, manifest, ["react"])


if __name__ == "__main__":
    unittest.main()

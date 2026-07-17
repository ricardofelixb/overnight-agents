from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "autonomous-pr-review" / "scripts" / "refresh_docs.py"
SPEC = importlib.util.spec_from_file_location("refresh_docs", SCRIPT)
assert SPEC and SPEC.loader
refresh_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh_docs)


class RefreshDocsTests(unittest.TestCase):
    def config(self) -> dict:
        return {
            "allowed_hosts": ["docs.example.test"],
            "urls": ["https://docs.example.test/llms.txt"],
        }

    def test_fresh_cache_avoids_a_second_network_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with mock.patch.object(
                refresh_docs,
                "fetch",
                return_value=(b"official guidance", "https://docs.example.test/llms.txt"),
            ) as fetch:
                first, errors = refresh_docs.refresh_domain(
                    "example", self.config(), cache, timedelta(hours=24), 30, 1000
                )
            self.assertEqual(errors, [])
            self.assertEqual(first[0]["source"], "network")
            fetch.assert_called_once()

            with mock.patch.object(refresh_docs, "fetch", side_effect=AssertionError("network should not run")):
                second, errors = refresh_docs.refresh_domain(
                    "example", self.config(), cache, timedelta(hours=24), 30, 1000
                )
            self.assertEqual(errors, [])
            self.assertEqual(second[0]["source"], "cache")
            self.assertEqual(second[0]["sha256"], first[0]["sha256"])

    def test_stale_cache_is_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with mock.patch.object(
                refresh_docs,
                "fetch",
                return_value=(b"old", "https://docs.example.test/llms.txt"),
            ):
                entries, errors = refresh_docs.refresh_domain(
                    "example", self.config(), cache, timedelta(hours=24), 30, 1000
                )
            self.assertEqual(errors, [])
            metadata_path = Path(entries[0]["content_path"]).with_suffix(".json")
            metadata = json.loads(metadata_path.read_text())
            metadata["retrieved_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            metadata_path.write_text(json.dumps(metadata))

            with mock.patch.object(
                refresh_docs,
                "fetch",
                return_value=(b"new", "https://docs.example.test/llms.txt"),
            ) as fetch:
                refreshed, errors = refresh_docs.refresh_domain(
                    "example", self.config(), cache, timedelta(hours=24), 30, 1000
                )
            self.assertEqual(errors, [])
            self.assertEqual(refreshed[0]["source"], "network")
            fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from review import (
    ReviewFailure,
    Runner,
    capture_review_context,
    fetch_pr_at_head,
    format_review_comment,
    legacy_head_was_reviewed,
    orchestrator_prompt,
    prepare_workspace,
    record_review_state,
    review_is_current,
    run_project_commands,
    tree_hash,
    upsert_review_comment,
    validate_docs_manifest,
    validate_skill_lock,
    write_validation_evidence,
)


class ReviewSafetyTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def repository_fixture(self, root: Path) -> tuple[Path, Path, str]:
        origin = root / "origin.git"
        seed = root / "seed"
        source = root / "source"
        self.git("init", "--bare", str(origin))
        self.git("init", "-b", "main", str(seed))
        self.git("config", "user.email", "reviewer@example.test", cwd=seed)
        self.git("config", "user.name", "Reviewer Test", cwd=seed)
        (seed / ".gitignore").write_text(".env.local\n")
        (seed / "example.txt").write_text("safe\n")
        self.git("add", ".gitignore", "example.txt", cwd=seed)
        self.git("commit", "-m", "initial", cwd=seed)
        self.git("remote", "add", "origin", str(origin), cwd=seed)
        self.git("push", "-u", "origin", "main", cwd=seed)
        head = self.git("rev-parse", "HEAD", cwd=seed)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
        self.git("update-ref", "refs/pull/1/head", head, cwd=origin)
        self.git("clone", str(origin), str(source))
        return origin, source, head

    def test_first_workspace_provision_checks_out_before_cleanliness_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, source, head = self.repository_fixture(root)
            workspace = root / "workspaces" / "example"
            runner = Runner(root / "review.log")
            prepare_workspace(runner, workspace, source, "trusted/example", 1, "main", head, head)
            self.assertEqual(self.git("rev-parse", "HEAD", cwd=workspace), head)
            self.assertEqual(self.git("status", "--porcelain", cwd=workspace), "")

    def test_incomplete_no_checkout_workspace_is_quarantined_and_reprovisioned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, source, head = self.repository_fixture(root)
            workspace = root / "workspaces" / "example"
            workspace.parent.mkdir(parents=True)
            self.git("clone", "--no-checkout", str(origin), str(workspace))
            self.assertTrue(self.git("status", "--porcelain", cwd=workspace))
            runner = Runner(root / "review.log")
            prepare_workspace(runner, workspace, source, "trusted/example", 1, "main", head, head)
            quarantined = list((workspace.parent / ".quarantine").iterdir())
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(self.git("status", "--porcelain", cwd=workspace), "")
            self.assertEqual(self.git("rev-parse", "HEAD", cwd=workspace), head)

    def test_workspace_provisions_private_environment_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, source, head = self.repository_fixture(root)
            environment = root / "private" / "example.env.local"
            environment.parent.mkdir()
            environment.write_text("EXAMPLE=value\n")
            environment.chmod(0o600)
            workspace = root / "workspaces" / "example"
            runner = Runner(root / "review.log")
            prepare_workspace(
                runner,
                workspace,
                source,
                "trusted/example",
                1,
                "main",
                head,
                head,
                environment,
            )
            self.assertTrue((workspace / ".env.local").is_symlink())
            self.assertEqual((workspace / ".env.local").resolve(), environment.resolve())
            self.assertEqual(self.git("status", "--porcelain", cwd=workspace), "")

    def test_workspace_rejects_public_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, source, head = self.repository_fixture(root)
            environment = root / "example.env.local"
            environment.write_text("EXAMPLE=value\n")
            environment.chmod(0o644)
            runner = Runner(root / "review.log")
            with self.assertRaisesRegex(ReviewFailure, "group or other"):
                prepare_workspace(
                    runner,
                    root / "workspaces" / "example",
                    source,
                    "trusted/example",
                    1,
                    "main",
                    head,
                    head,
                    environment,
                )

    def test_orchestrator_prompt_requires_subagents_and_allows_proven_repairs(self) -> None:
        root = Path("/tmp/reviewer-test")
        prompt = orchestrator_prompt(**{
            "skill_path": root / "skill",
            "pr": {"url": "https://example.test/pr/1", "number": 1, "baseRefOid": "a" * 40, "headRefOid": "b" * 40},
            "changed_files_path": root / "changed.txt",
            "docs_manifest": root / "docs.json",
            "skills_manifest": root / "skills.json",
            "review_context": root / "context.json",
            "validation_evidence": root / "validation.json",
            "repairs_allowed": True,
        })
        self.assertIn("Spawn the three named specialist sub-agents concurrently", prompt)
        self.assertIn("pre-existed at the base", prompt)
        self.assertIn(str(root / "context.json"), prompt)
        self.assertIn("repairs allowed: true", prompt)
        self.assertIn("trusted menus, not mandatory context", prompt)
        self.assertIn("Do not open every skill or document", prompt)

    def test_comment_reports_clean_and_repaired_outcomes(self) -> None:
        clean = {
            "status": "clean",
            "repairs": [],
            "verification": {"performed": False},
            "remaining_observations": [],
            "blocking_reasons": [],
        }
        clean_comment = format_review_comment(
            clean,
            original_head="a" * 40,
            final_head="a" * 40,
            validation_commands=[["pnpm", "run", "validate"]],
        )
        self.assertIn("safe to merge", clean_comment)
        self.assertIn("pnpm run validate", clean_comment)
        repaired = clean | {
            "status": "repaired",
            "repairs": [{
                "title": "Fix access check",
                "provenance": "pre_existing",
                "evidence": "A cross-tenant read was possible",
            }],
            "verification": {"performed": True, "summary": "verified"},
        }
        repaired_comment = format_review_comment(
            repaired,
            original_head="a" * 40,
            final_head="b" * 40,
            validation_commands=[["pnpm", "run", "validate"]],
        )
        self.assertIn("fixed and safe to merge", repaired_comment)
        self.assertIn("pre-existing", repaired_comment)
        self.assertIn("Repair commit", repaired_comment)
        repaired_blocked = repaired | {
            "status": "repaired_blocked",
            "blocking_reasons": ["product decision required"],
        }
        repaired_blocked_comment = format_review_comment(
            repaired_blocked,
            original_head="a" * 40,
            final_head="b" * 40,
            validation_commands=[["pnpm", "run", "validate"]],
        )
        self.assertIn("improved, decision still required", repaired_blocked_comment)
        self.assertIn("product decision required", repaired_blocked_comment)
        self.assertIn("pnpm run validate", repaired_blocked_comment)

    def test_review_state_skips_only_identical_head_and_pr_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pr = {"number": 7, "headRefOid": "a" * 40, "updatedAt": "2026-07-16T12:00:00Z"}
            record_review_state(root, "example", pr, "clean")
            self.assertTrue(review_is_current(root, "example", pr))
            self.assertFalse(review_is_current(root, "example", pr | {"updatedAt": "2026-07-16T12:01:00Z"}))
            self.assertFalse(review_is_current(root, "example", pr | {"headRefOid": "b" * 40}))

    def test_legacy_review_state_migrates_only_the_same_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = root / "runs" / "example" / "7" / "old" / "summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"status": "blocked", "head_sha": "a" * 40}))
            self.assertTrue(legacy_head_was_reviewed(root, "example", {"number": 7, "headRefOid": "a" * 40}))
            self.assertFalse(legacy_head_was_reviewed(root, "example", {"number": 7, "headRefOid": "b" * 40}))

    def test_review_comment_updates_only_the_controller_marker(self) -> None:
        class CommentRunner:
            def __init__(self, existing_body: str | None) -> None:
                self.existing_body = existing_body
                self.commands: list[list[str]] = []

            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                self.commands.append(command)
                joined = " ".join(command)
                if "viewer{login}" in joined:
                    nodes = [] if self.existing_body is None else [{
                        "id": "COMMENT_ID",
                        "body": self.existing_body,
                        "author": {"login": "controller"},
                    }]
                    payload = {"data": {
                        "viewer": {"login": "controller"},
                        "repository": {"pullRequest": {"comments": {"nodes": nodes}}},
                    }}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                return subprocess.CompletedProcess(command, 0, json.dumps({"data": {}}), "")

        pr = {"number": 7, "id": "PR_ID"}
        created = CommentRunner(None)
        upsert_review_comment(created, "trusted/example", pr, "<!-- overnight-agents:pr-reviewer -->\nclean")
        self.assertTrue(any("addComment" in " ".join(command) for command in created.commands))
        updated = CommentRunner("<!-- overnight-agents:pr-reviewer -->\nold")
        upsert_review_comment(updated, "trusted/example", pr, "<!-- overnight-agents:pr-reviewer -->\nnew")
        self.assertTrue(any("updateIssueComment" in " ".join(command) for command in updated.commands))

    def test_validation_evidence_is_sha_bound_and_hashes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_validation_evidence(
                root,
                0,
                {"baseRefOid": "a" * 40, "headRefOid": "b" * 40},
                [["pnpm", "install"]],
                [["pnpm", "run", "validate"]],
                ["complete"],
                {"NODE_OPTIONS": "--max-old-space-size=8192"},
                "installed\n",
                "complete\n",
            )
            evidence = json.loads(path.read_text())
            self.assertEqual(evidence["head_sha"], "b" * 40)
            self.assertEqual(evidence["status"], "passed")
            self.assertEqual(evidence["validation_environment"]["NODE_OPTIONS"], "--max-old-space-size=8192")
            output = Path(evidence["validation_output_path"])
            self.assertEqual(
                evidence["validation_output_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )

    def test_validation_retries_the_complete_gate_once(self) -> None:
        class RetryRunner:
            def __init__(self) -> None:
                self.calls = 0
                self.messages: list[str] = []

            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                self.calls += 1
                return subprocess.CompletedProcess(
                    command,
                    1 if self.calls == 1 else 0,
                    "transient failure\n" if self.calls == 1 else "all passed\n",
                    "",
                )

            def log(self, message: str) -> None:
                self.messages.append(message)

        runner = RetryRunner()
        output = run_project_commands(
            runner,  # type: ignore[arg-type]
            Path("/tmp"),
            [["validate"]],
            ["passed"],
            attempts=2,
        )
        self.assertEqual(runner.calls, 2)
        self.assertIn("transient failure", output)
        self.assertIn("all passed", output)
        self.assertEqual(len(runner.messages), 1)

    def test_pr_head_projection_is_polled_after_push(self) -> None:
        class ProjectionRunner:
            def __init__(self) -> None:
                self.calls = 0
                self.messages: list[str] = []

            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                self.calls += 1
                head = "a" * 40 if self.calls == 1 else "b" * 40
                return subprocess.CompletedProcess(command, 0, json.dumps({"headRefOid": head}), "")

            def log(self, message: str) -> None:
                self.messages.append(message)

        runner = ProjectionRunner()
        with mock.patch("review.time.sleep") as sleep:
            pr = fetch_pr_at_head(
                runner,  # type: ignore[arg-type]
                "trusted/example",
                1,
                "b" * 40,
                attempts=2,
            )
        self.assertEqual(pr["headRefOid"], "b" * 40)
        self.assertEqual(runner.calls, 2)
        sleep.assert_called_once_with(1)

    def test_review_context_is_sha_bound_and_size_limited(self) -> None:
        class GraphqlRunner:
            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                payload = {
                    "data": {"repository": {"pullRequest": {"reviewThreads": {
                        "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}
                    }}}}
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        pr = {
            "number": 1,
            "url": "https://example.test/pr/1",
            "baseRefOid": "a" * 40,
            "headRefOid": "b" * 40,
            "title": "Example",
            "body": "A correctness claim",
            "commits": [],
            "comments": [
                {"author": {"login": "controller"}, "body": "<!-- overnight-agents:pr-reviewer -->\nold"},
                {"author": {"login": "trusted"}, "body": "Please verify the empty state"},
            ],
            "reviews": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = capture_review_context(
                GraphqlRunner(), {"max_review_context_bytes": 10000}, "trusted/example", pr, root, 0, "controller"
            )
            context = json.loads(path.read_text())
            self.assertEqual(context["base_sha"], "a" * 40)
            self.assertEqual(context["head_sha"], "b" * 40)
            self.assertEqual([item["body"] for item in context["comments"]], ["Please verify the empty state"])
            with self.assertRaisesRegex(ReviewFailure, "evidence limit"):
                capture_review_context(
                    GraphqlRunner(), {"max_review_context_bytes": 10}, "trusted/example", pr, root, 1, "controller"
                )

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

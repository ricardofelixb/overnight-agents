from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller import (
    ReviewFailure,
    Runner,
    capture_ai_files_snapshot,
    capture_ci_context,
    capture_review_context,
    carry_simplification_state_to_head,
    commit_simplification,
    current_simplification_state,
    fetch_pr_at_head,
    format_review_comment,
    legacy_head_was_reviewed,
    orchestrator_prompt,
    prepare_workspace,
    push_final_head,
    record_simplification_state,
    record_review_state,
    repository_runtime_path,
    require_green_github_ci,
    reject_policy_changes,
    reject_protected_agent_edits,
    review_is_current,
    run_orchestrator,
    run_setup_commands,
    setup_agent_workspace,
    run_simplification_phase,
    simplification_is_skipped,
    simplification_prompt,
    tree_hash,
    upsert_review_comment,
    validate_docs_manifest,
    validate_orchestrator_result,
    validate_skill_lock,
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

    def test_dependency_manifests_are_reviewable_but_not_agent_editable(self) -> None:
        config = {
            "protected_policy_patterns": ["AGENTS.md", ".github/**"],
            "protected_agent_edit_patterns": ["package.json", "pnpm-lock.yaml"],
        }
        reject_policy_changes(["package.json", "pnpm-lock.yaml"], config["protected_policy_patterns"])
        with self.assertRaisesRegex(ReviewFailure, "agent changed protected files"):
            reject_protected_agent_edits(["package.json"], config)
        with self.assertRaisesRegex(ReviewFailure, "trusted agent/review policy"):
            reject_policy_changes(["AGENTS.md"], config["protected_policy_patterns"])

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
            "ci_context": root / "ci-context.json",
            "ai_files_manifest": root / "ai-files.json",
            "repairs_allowed": True,
            "validation_commands": [["pnpm", "run", "validate"]],
            "validation_env": {"NODE_OPTIONS": "--max-old-space-size=8192"},
        })
        self.assertIn("Spawn the three named specialist sub-agents concurrently", prompt)
        self.assertIn("pre-existed at the base", prompt)
        self.assertIn(str(root / "context.json"), prompt)
        self.assertIn(str(root / "ci-context.json"), prompt)
        self.assertIn("You own validation", prompt)
        self.assertIn('[["pnpm", "run", "validate"]]', prompt)
        self.assertIn("will not rerun validation or launch a correction cycle", prompt)
        self.assertIn("repairs allowed: true", prompt)
        self.assertIn("trusted menus, not mandatory context", prompt)
        self.assertIn("Do not open every skill or document", prompt)
        self.assertIn(str(root / "ai-files.json"), prompt)
        self.assertIn("generated guidelines", prompt)
        self.assertIn("verification.verdict", prompt)
        self.assertIn("fresh independent verifier", prompt)

    def test_orchestrator_runs_once_without_controller_correction_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = mock.Mock()
            runner.log = mock.Mock()

            def write_result(*_: object, **kwargs: object) -> None:
                output = kwargs.get("output")
                if not isinstance(output, Path):
                    output = _[4]
                assert isinstance(output, Path)
                output.write_text(json.dumps({"source": output.name}))

            with (
                mock.patch("controller.run_codex_result", side_effect=write_result) as run_codex,
                mock.patch(
                    "controller.validate_orchestrator_result",
                    return_value={"status": "repaired"},
                ) as validate,
            ):
                result = run_orchestrator(
                    runner,
                    {
                        "skill_path": str(root / "skill"),
                    },
                    root / "workspace",
                    {
                        "url": "https://example.test/pr/1",
                        "number": 1,
                        "baseRefOid": "a" * 40,
                        "headRefOid": "b" * 40,
                    },
                    root,
                    root / "changed.txt",
                    root / "docs.json",
                    root / "skills.json",
                    root / "review.json",
                    root / "ci.json",
                    None,
                    True,
                    [["pnpm", "run", "validate"]],
                    {},
                )
            self.assertEqual(result, {"status": "repaired"})
            self.assertEqual(run_codex.call_count, 1)
            self.assertEqual(validate.call_count, 1)
            canonical = json.loads((root / "orchestrator-result.json").read_text())
            self.assertEqual(canonical["source"], "orchestrator-result.json")
            runner.log.assert_not_called()

    def test_simplification_prompt_is_exact_sha(self) -> None:
        root = Path("/tmp/reviewer-test")
        pr = {
            "url": "https://example.test/pr/1",
            "number": 1,
            "baseRefOid": "a" * 40,
            "headRefOid": "b" * 40,
        }
        prompt = simplification_prompt(
            skill_path=root / "skill",
            pr=pr,
            changed_files_path=root / "changed.txt",
            edits_allowed=True,
            validation_commands=[["pnpm", "run", "validate"]],
            validation_env={},
        )
        self.assertIn("three named read-only specialist sub-agents concurrently", prompt)
        self.assertIn("base SHA: " + "a" * 40, prompt)
        self.assertIn("head SHA: " + "b" * 40, prompt)
        self.assertIn("edits allowed: true", prompt)
        self.assertIn("You own validation", prompt)
        self.assertIn("will not rerun the suite", prompt)

    def test_repository_runtime_uses_declared_node_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".nvmrc").write_text("24.16.0\n")
            runtime = (
                root
                / ".local/share/fnm/node-versions/v24.16.0/installation/bin"
            )
            runtime.mkdir(parents=True)
            (runtime / "node").touch()
            with mock.patch("controller.shared_runtime.Path.home", return_value=root):
                path = repository_runtime_path(workspace, "/usr/bin:/bin")
            self.assertEqual(path, f"{runtime}:/usr/bin:/bin")

    def test_controller_uses_working_tree_as_result_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result.json"
            output.write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "changed_files": ["wrong.ts"],
                        "blocking_reasons": ["product decision"],
                    }
                )
            )
            with mock.patch(
                "controller.workspace_changes", return_value={"src/fixed.ts"}
            ):
                result = validate_orchestrator_result(
                    mock.Mock(),
                    {"protected_agent_edit_patterns": []},
                    root / "workspace",
                    {"baseRefOid": "a" * 40, "headRefOid": "b" * 40},
                    output,
                    root / "changed.txt",
                    root / "docs.json",
                    root / "skills.json",
                )
            self.assertEqual(result["status"], "repaired_blocked")
            self.assertEqual(result["changed_files"], ["src/fixed.ts"])
    def test_human_pr_simplification_is_default_and_automation_branch_skips(self) -> None:
        project: dict[str, object] = {}
        self.assertIsNone(simplification_is_skipped({"headRefName": "feature/import"}, project))
        self.assertEqual(
            simplification_is_skipped({"headRefName": "code-simplify/convex"}, project),
            "already_simplified_automation",
        )
        self.assertEqual(
            simplification_is_skipped({"headRefName": "code-maintain/calendar"}, project),
            "already_simplified_automation",
        )
        self.assertEqual(
            simplification_is_skipped(
                {"headRefName": "feature/import"}, project | {"simplify_human_prs": False}
            ),
            "disabled",
        )

    def test_simplification_state_is_exact_head_and_preserves_commit_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pr = {"number": 7, "headRefOid": "b" * 40}
            state = record_simplification_state(
                root,
                "example",
                pr,
                input_head_sha="a" * 40,
                result={"status": "simplified", "summary": "Reused helper.", "improvements": [{}]},
                reason="simplification_commit",
            )
            self.assertEqual(current_simplification_state(root, "example", pr), state)
            self.assertEqual(state["simplification_head_sha"], "b" * 40)
            self.assertIsNone(
                current_simplification_state(root, "example", pr | {"headRefOid": "c" * 40})
            )
            carried = carry_simplification_state_to_head(
                root,
                "example",
                pr | {"headRefOid": "c" * 40},
                state,
            )
            self.assertEqual(carried["reason"], "simplification_commit")
            self.assertEqual(carried["head_sha"], "c" * 40)
            self.assertEqual(carried["simplification_head_sha"], "b" * 40)
            self.assertEqual(carried["finalized_by"], "controller_output")

    def test_simplification_checkpoint_stays_local_until_atomic_push(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, workspace, _ = self.repository_fixture(root)
            self.git("checkout", "-b", "feature/atomic", cwd=workspace)
            (workspace / "example.txt").write_text("human change\n")
            self.git("add", "example.txt", cwd=workspace)
            self.git("commit", "-m", "human change", cwd=workspace)
            self.git("push", "-u", "origin", "feature/atomic", cwd=workspace)
            remote_input = self.git("rev-parse", "HEAD", cwd=workspace)
            (workspace / "example.txt").write_text("simplified change\n")
            runner = Runner(root / "atomic.log")
            local_head = commit_simplification(
                runner,
                workspace,
                {"number": 7, "headRefName": "feature/atomic"},
            )
            remote_before = self.git(
                "--git-dir", str(origin), "rev-parse", "refs/heads/feature/atomic"
            )
            self.assertEqual(remote_before, remote_input)
            self.assertNotEqual(local_head, remote_input)
            pushed = push_final_head(
                runner,
                workspace,
                {"headRefName": "feature/atomic"},
                expected_remote_head=remote_input,
            )
            self.assertEqual(pushed, local_head)
            self.assertIn(
                f"--force-with-lease=refs/heads/feature/atomic:{remote_input}",
                (root / "atomic.log").read_text(),
            )
            self.assertEqual(
                self.git("--git-dir", str(origin), "rev-parse", "refs/heads/feature/atomic"),
                local_head,
            )

    def test_atomic_push_refuses_a_concurrent_human_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin, workspace, _ = self.repository_fixture(root)
            self.git("checkout", "-b", "feature/race", cwd=workspace)
            (workspace / "example.txt").write_text("human change\n")
            self.git("add", "example.txt", cwd=workspace)
            self.git("commit", "-m", "human change", cwd=workspace)
            self.git("push", "-u", "origin", "feature/race", cwd=workspace)
            remote_input = self.git("rev-parse", "HEAD", cwd=workspace)
            (workspace / "example.txt").write_text("local automation change\n")
            self.git("add", "example.txt", cwd=workspace)
            self.git("commit", "-m", "local automation", cwd=workspace)

            contender = root / "contender"
            self.git("clone", str(origin), str(contender))
            self.git("config", "user.email", "human@example.test", cwd=contender)
            self.git("config", "user.name", "Human", cwd=contender)
            self.git("checkout", "feature/race", cwd=contender)
            (contender / "concurrent.txt").write_text("new human work\n")
            self.git("add", "concurrent.txt", cwd=contender)
            self.git("commit", "-m", "concurrent human push", cwd=contender)
            self.git("push", "origin", "feature/race", cwd=contender)
            concurrent_head = self.git("rev-parse", "HEAD", cwd=contender)

            with self.assertRaisesRegex(ReviewFailure, "refusing to push"):
                push_final_head(
                    Runner(root / "race.log"),
                    workspace,
                    {"headRefName": "feature/race"},
                    expected_remote_head=remote_input,
                )
            self.assertEqual(
                self.git("--git-dir", str(origin), "rev-parse", "refs/heads/feature/race"),
                concurrent_head,
            )

    def test_atomic_push_requires_a_descendant_of_the_original_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, workspace, _ = self.repository_fixture(root)
            self.git("checkout", "-b", "feature/descendant", cwd=workspace)
            (workspace / "feature.txt").write_text("human change\n")
            self.git("add", "feature.txt", cwd=workspace)
            self.git("commit", "-m", "human change", cwd=workspace)
            self.git("push", "-u", "origin", "feature/descendant", cwd=workspace)
            remote_input = self.git("rev-parse", "HEAD", cwd=workspace)
            self.git("checkout", "main", cwd=workspace)
            with self.assertRaisesRegex(ReviewFailure, "not a descendant"):
                push_final_head(
                    Runner(root / "descendant.log"),
                    workspace,
                    {"headRefName": "feature/descendant"},
                    expected_remote_head=remote_input,
                )

    def test_clean_human_head_runs_simplifier_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            workspace = root / "workspace"
            workspace.mkdir()
            pr = {
                "number": 7,
                "url": "https://example.test/pr/7",
                "baseRefOid": "a" * 40,
                "headRefOid": "b" * 40,
                "headRefName": "feature/import",
            }
            project = {
                "repository": "trusted/example",
                "validation_commands": [["true"]],
                "setup_commands": [],
                "validation_environment": {},
                "mode": "repair",
                "protected_policy_patterns": [],
            }
            result = {
                "status": "clean",
                "summary": "No worthwhile simplification.",
                "improvements": [],
                "remaining_observations": [],
                "blocking_reasons": [],
            }
            runner = Runner(root / "review.log")
            with mock.patch("controller.changed_files_and_diff", return_value=(["src/a.ts"], "diff")), mock.patch(
                "controller.assert_clean_workspace"
            ), mock.patch(
                "controller.workspace_changes", return_value=set()
            ), mock.patch(
                "controller.run_simplifier_orchestrator",
                return_value=(result, root / "result.json"),
            ) as orchestrate:
                current, state = run_simplification_phase(
                    runner,
                    {"state_root": str(root / "state")},
                    "example",
                    project,
                    workspace,
                    pr,
                    run_dir,
                    [["true"]],
                    {},
                    apply=True,
                )
                self.assertEqual(current, pr)
                self.assertEqual(state["status"], "clean")
                second, second_state = run_simplification_phase(
                    runner,
                    {"state_root": str(root / "state")},
                    "example",
                    project,
                    workspace,
                    pr,
                    run_dir,
                    [["true"]],
                    {},
                    apply=True,
                )
                self.assertEqual(second, pr)
                self.assertEqual(second_state, state)
                orchestrate.assert_called_once()

    def test_github_ci_gate_requires_checks_to_be_green(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "ci.json"
            context.write_text(json.dumps({"checks": [{"bucket": "pending"}]}))
            with self.assertRaisesRegex(ReviewFailure, "still pending"):
                require_green_github_ci(context)
            context.write_text(
                json.dumps({"checks": [{"bucket": "pass"}, {"bucket": "skipping"}]})
            )
            require_green_github_ci(context)

    def test_convex_ai_files_snapshot_is_fresh_and_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "state" / "ai-files" / "example" / "releases" / ("a" * 64)
            guidelines = release / "convex" / "_generated" / "ai" / "guidelines.md"
            guidelines.parent.mkdir(parents=True)
            content = b"# Current Convex guidance\n"
            guidelines.write_bytes(content)
            manifest = {
                "version": 1,
                "project": "example",
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "release_path": str(release),
                "files": {
                    "convex/_generated/ai/guidelines.md": {
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                },
            }
            manifest_path = root / "state" / "ai-files" / "example" / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            run_dir = root / "run"
            run_dir.mkdir()
            captured = capture_ai_files_snapshot(
                {"state_root": str(root / "state"), "ai_files_max_age_days": 8},
                "example",
                ["convex"],
                run_dir,
            )
            self.assertEqual(captured, run_dir / "convex-ai-files-manifest.json")
            guidelines.write_text("tampered")
            with self.assertRaisesRegex(ReviewFailure, "integrity mismatch"):
                capture_ai_files_snapshot(
                    {"state_root": str(root / "state"), "ai_files_max_age_days": 8},
                    "example",
                    ["convex"],
                    run_dir,
                )

    def test_comment_reports_clean_and_repaired_outcomes(self) -> None:
        clean = {
            "status": "clean",
            "repairs": [],
            "verification": {"performed": False},
            "manual_ui_checks": [],
            "remaining_observations": [],
            "blocking_reasons": [],
        }
        clean_comment = format_review_comment(
            clean,
            original_head="a" * 40,
            final_head="a" * 40,
        )
        self.assertIn("safe to merge", clean_comment)
        self.assertIn("Exact-head GitHub CI was green", clean_comment)
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
        )
        self.assertIn("fixed and safe to merge", repaired_comment)
        self.assertIn("pre-existing", repaired_comment)
        self.assertIn("Repair commit", repaired_comment)
        self.assertIn("GitHub CI remains authoritative", repaired_comment)
        repaired_blocked = repaired | {
            "status": "repaired_blocked",
            "blocking_reasons": ["product decision required"],
        }
        repaired_blocked_comment = format_review_comment(
            repaired_blocked,
            original_head="a" * 40,
            final_head="b" * 40,
        )
        self.assertIn("improved, decision still required", repaired_blocked_comment)
        self.assertIn("product decision required", repaired_blocked_comment)
        self.assertIn("GitHub CI remains authoritative", repaired_blocked_comment)

        simplified_comment = format_review_comment(
            clean,
            original_head="b" * 40,
            final_head="b" * 40,
            simplification={
                "status": "simplified",
                "reason": "simplification_commit",
                "input_head_sha": "a" * 40,
                "head_sha": "c" * 40,
                "simplification_head_sha": "b" * 40,
                "improvements": [{"id": "reuse-helper"}],
            },
        )
        self.assertIn("Implementation simplification", simplified_comment)
        self.assertIn(("b" * 12), simplified_comment)
        self.assertNotIn(("c" * 12), simplified_comment)

    def test_comment_includes_only_supplied_manual_ui_checks(self) -> None:
        result = {
            "status": "clean",
            "repairs": [],
            "verification": {"performed": False},
            "manual_ui_checks": [
                "Create an expense and confirm the new row shows the selected supplier.",
                "Search expense categories and confirm pagination preserves the query.",
            ],
            "remaining_observations": [],
            "blocking_reasons": [],
        }
        comment = format_review_comment(
            result,
            original_head="a" * 40,
            final_head="a" * 40,
        )
        self.assertIn("### Manual UI sanity checks", comment)
        self.assertIn("- [ ] Create an expense", comment)
        self.assertIn("- [ ] Search expense categories", comment)

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

    def test_setup_commands_run_once(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, "installed\n", "")
        output = run_setup_commands(
            runner,
            Path("/tmp"),
            [["pnpm", "install"]],
            {"NODE_OPTIONS": "--max-old-space-size=8192"},
            repository_workspace=Path("/tmp"),
        )
        self.assertEqual(output, "installed\n")
        runner.run.assert_called_once()
        self.assertNotEqual(runner.run.call_args.kwargs.get("check"), False)

    def test_repository_workspace_hooks_wrap_the_top_level_agent_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = root / "trusted-source"
            workspace.mkdir()
            source.mkdir()
            runner = mock.Mock()
            runner.log_path = root / "review.log"
            project = {
                "workspace_hooks": {
                    "setup_command": ["scripts/setup-worktree.sh", "--convex-mode", "local"],
                    "cleanup_command": ["scripts/cleanup-worktree.sh"],
                }
            }

            with mock.patch("controller.worktree_lifecycle.run_setup_hook") as setup_hook, mock.patch(
                "controller.worktree_lifecycle.run_cleanup_hook"
            ) as cleanup_hook:
                with ExitStack() as lifecycle:
                    setup_agent_workspace(
                        runner,
                        project,
                        workspace,
                        source,
                        lifecycle,
                    )
                    setup_hook.assert_called_once()
                    self.assertEqual(
                        setup_hook.call_args.kwargs["hook_root"], source
                    )
                    cleanup_hook.assert_not_called()

                cleanup_hook.assert_called_once()
                self.assertEqual(
                    cleanup_hook.call_args.kwargs["hook_root"], source
                )

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
        with mock.patch("controller.time.sleep") as sleep:
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
                {"author": {"login": "controller"}, "body": "<!-- overnight-agents-progress:delivery-1 -->\nrunning"},
                {"author": {"login": "trusted"}, "body": "Please verify the empty state"},
            ],
            "reviews": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = capture_review_context(
                GraphqlRunner(),
                {"max_review_context_bytes": 10000},
                "trusted/example",
                pr,
                root,
                0,
                "controller",
                github_head_sha="c" * 40,
            )
            context = json.loads(path.read_text())
            self.assertEqual(context["base_sha"], "a" * 40)
            self.assertEqual(context["head_sha"], "b" * 40)
            self.assertEqual(context["reviewed_head_sha"], "b" * 40)
            self.assertEqual(context["github_head_sha"], "c" * 40)
            self.assertEqual([item["body"] for item in context["comments"]], ["Please verify the empty state"])
            with self.assertRaisesRegex(ReviewFailure, "evidence limit"):
                capture_review_context(
                    GraphqlRunner(), {"max_review_context_bytes": 10}, "trusted/example", pr, root, 1, "controller"
                )

    def test_ci_context_retains_failed_step_logs_and_tolerates_no_checks(self) -> None:
        class CiRunner:
            def __init__(self, checks: subprocess.CompletedProcess[str]) -> None:
                self.checks = checks

            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[1:3] == ["pr", "checks"]:
                    return self.checks
                return subprocess.CompletedProcess(command, 0, "job\tstep\tType error\n", "")

        pr = {"number": 7, "headRefOid": "b" * 40}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed = subprocess.CompletedProcess(
                ["gh"],
                1,
                json.dumps([{
                    "bucket": "fail",
                    "link": "https://github.com/trusted/example/actions/runs/123/job/456",
                    "name": "validate",
                }]),
                "",
            )
            path = capture_ci_context(
                CiRunner(failed),  # type: ignore[arg-type]
                {"max_ci_context_bytes": 10000},
                "trusted/example",
                pr,
                root,
                github_head_sha="c" * 40,
            )
            context = json.loads(path.read_text())
            self.assertEqual(context["head_sha"], "b" * 40)
            self.assertEqual(context["reviewed_head_sha"], "b" * 40)
            self.assertEqual(context["github_head_sha"], "c" * 40)
            self.assertIn("Type error", context["failed_run_logs"]["123"])

            unavailable = subprocess.CompletedProcess(["gh"], 1, "no checks reported", "")
            path = capture_ci_context(
                CiRunner(unavailable),  # type: ignore[arg-type]
                {"max_ci_context_bytes": 10000},
                "trusted/example",
                pr,
                root,
            )
            context = json.loads(path.read_text())
            self.assertEqual(context["checks"], [])
            self.assertEqual(context["checks_command_error"], "no checks reported")

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

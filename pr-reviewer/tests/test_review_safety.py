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
    ResultContractFailure,
    Runner,
    bind_validation_evidence_to_checkpoint,
    capture_ai_files_snapshot,
    capture_ci_context,
    capture_review_context,
    carry_simplification_state_to_head,
    collect_project_commands,
    commit_simplification,
    correction_prompt,
    current_simplification_state,
    fetch_pr_at_head,
    format_review_comment,
    legacy_head_was_reviewed,
    orchestrator_prompt,
    prepare_workspace,
    push_final_head,
    record_simplification_state,
    record_review_state,
    result_contract_correction_prompt,
    review_is_current,
    run_orchestrator,
    run_project_commands,
    run_simplification_phase,
    simplification_correction_prompt,
    simplification_is_skipped,
    simplification_prompt,
    tree_hash,
    upsert_review_comment,
    validate_docs_manifest,
    validate_orchestrator_result,
    validate_repair_with_corrections,
    validate_skill_lock,
    workspace_fingerprint,
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

    def test_workspace_fingerprint_refuses_symlink_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            target = workspace / "target.txt"
            target.write_text("sensitive\n")
            (workspace / "link.txt").symlink_to(target)
            runner = mock.Mock()
            runner.run.side_effect = [
                subprocess.CompletedProcess([], 0, "link.txt\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            with self.assertRaisesRegex(ReviewFailure, "symlink"):
                workspace_fingerprint(runner, workspace)

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
            "validation_evidence": root / "validation.json",
            "ai_files_manifest": root / "ai-files.json",
            "repairs_allowed": True,
            "simplification_context": root / "simplification.json",
        })
        self.assertIn("Spawn the three named specialist sub-agents concurrently", prompt)
        self.assertIn("pre-existed at the base", prompt)
        self.assertIn(str(root / "context.json"), prompt)
        self.assertIn(str(root / "ci-context.json"), prompt)
        self.assertIn("validation gate as a primary finding", prompt)
        self.assertIn("repairs allowed: true", prompt)
        self.assertIn("trusted menus, not mandatory context", prompt)
        self.assertIn("Do not open every skill or document", prompt)
        self.assertIn(str(root / "ai-files.json"), prompt)
        self.assertIn("generated guidelines", prompt)
        self.assertIn(str(root / "simplification.json"), prompt)
        self.assertIn("untrusted lead", prompt)
        self.assertIn("verification.verdict", prompt)
        self.assertIn("fresh independent verifier", prompt)

    def test_correction_prompt_resumes_without_restarting_specialists(self) -> None:
        root = Path("/tmp/reviewer-test")
        prompt = correction_prompt(
            skill_path=root / "skill",
            pr={"url": "https://example.test/pr/1", "number": 1, "baseRefOid": "a" * 40, "headRefOid": "b" * 40},
            changed_files_path=root / "changed.txt",
            prior_result=root / "result.json",
            validation_evidence=root / "validation.json",
        )
        self.assertIn("bounded validation-correction cycle, not a new review", prompt)
        self.assertIn("Do not restart the three specialist reviews", prompt)
        self.assertIn(str(root / "result.json"), prompt)
        self.assertIn(str(root / "validation.json"), prompt)
        self.assertIn("spawn one fresh read-only verifier", prompt)

    def test_result_contract_correction_is_bounded_and_result_only(self) -> None:
        root = Path("/tmp/reviewer-test")
        prompt = result_contract_correction_prompt(
            skill_path=root / "skill",
            pr={"url": "https://example.test/pr/1", "number": 1, "baseRefOid": "a" * 40, "headRefOid": "b" * 40},
            changed_files_path=root / "changed.txt",
            invalid_result=root / "invalid.json",
            contract_errors=root / "errors.txt",
        )
        self.assertIn("bounded result-contract correction, not a new review", prompt)
        self.assertIn("Do not restart specialist reviews", prompt)
        self.assertIn("verification.verdict", prompt)
        self.assertIn(str(root / "invalid.json"), prompt)
        self.assertIn(str(root / "errors.txt"), prompt)

    def test_semantic_result_contract_failure_is_typed(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = subprocess.CompletedProcess(
            [],
            1,
            "repaired_blocked result requires passed verification\n",
            "",
        )
        with self.assertRaisesRegex(ResultContractFailure, "passed verification"):
            validate_orchestrator_result(
                runner,
                {"skill_path": "/tmp/skill"},
                Path("/tmp/workspace"),
                {"baseRefOid": "a" * 40, "headRefOid": "b" * 40},
                Path("/tmp/result.json"),
                Path("/tmp/changed.txt"),
                Path("/tmp/docs.json"),
                Path("/tmp/skills.json"),
            )
        self.assertFalse(runner.run.call_args.kwargs["check"])

    def test_orchestrator_self_corrects_one_semantic_result_failure(self) -> None:
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
                mock.patch("review.run_codex_result", side_effect=write_result) as run_codex,
                mock.patch(
                    "review.validate_orchestrator_result",
                    side_effect=[
                        ResultContractFailure("verifier verdict mismatch"),
                        {"status": "repaired"},
                    ],
                ) as validate,
            ):
                result = run_orchestrator(
                    runner,
                    {
                        "skill_path": str(root / "skill"),
                        "result_contract_correction_cycles": 1,
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
                    root / "validation.json",
                    None,
                    None,
                    True,
                )
            self.assertEqual(result, {"status": "repaired"})
            self.assertEqual(run_codex.call_count, 2)
            self.assertEqual(validate.call_count, 2)
            canonical = json.loads((root / "orchestrator-result.json").read_text())
            self.assertEqual(
                canonical["source"],
                "orchestrator-result-contract-correction-1.json",
            )
            runner.log.assert_called_once()

    def test_simplification_prompts_are_exact_sha_and_corrections_do_not_restart(self) -> None:
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
        )
        self.assertIn("three named read-only specialist sub-agents concurrently", prompt)
        self.assertIn("base SHA: " + "a" * 40, prompt)
        self.assertIn("head SHA: " + "b" * 40, prompt)
        self.assertIn("edits allowed: true", prompt)
        correction = simplification_correction_prompt(
            skill_path=root / "skill",
            pr=pr,
            changed_files_path=root / "changed.txt",
            prior_result=root / "result.json",
            validation_evidence=root / "validation.json",
        )
        self.assertIn("Do not rerun the three specialist reviews", correction)
        self.assertIn(str(root / "result.json"), correction)
        self.assertIn(str(root / "validation.json"), correction)

    def test_human_pr_simplification_is_default_and_automation_branch_skips(self) -> None:
        project: dict[str, object] = {}
        self.assertIsNone(simplification_is_skipped({"headRefName": "feature/import"}, project))
        self.assertEqual(
            simplification_is_skipped({"headRefName": "code-simplify/convex"}, project),
            "already_simplified_automation",
        )
        self.assertEqual(
            simplification_is_skipped({"headRefName": "code-organize/sales"}, project),
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
            self.assertEqual(carried["finalized_by"], "reviewer_output")

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
            evidence_path = root / "validation-evidence.json"
            evidence_path.write_text(json.dumps({"head_sha": remote_input, "status": "passed"}))
            bound_path = bind_validation_evidence_to_checkpoint(
                runner,
                workspace,
                evidence_path,
                original_head=remote_input,
                checkpoint_head=local_head,
                validated_fingerprint={
                    "example.txt": hashlib.sha256((workspace / "example.txt").read_bytes()).hexdigest()
                },
                output=root / "bound-validation-evidence.json",
            )
            bound = json.loads(bound_path.read_text())
            self.assertEqual(bound["source_head_sha"], remote_input)
            self.assertEqual(bound["head_sha"], local_head)
            self.assertEqual(
                bound["checkpoint_tree_sha"],
                self.git("rev-parse", f"{local_head}^{{tree}}", cwd=workspace),
            )
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
                "validation_success_markers": [],
                "validation_environment": {},
                "validation_attempts": 1,
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
            with mock.patch("review.changed_files_and_diff", return_value=(["src/a.ts"], "diff")), mock.patch(
                "review.run_project_commands", return_value=[]
            ), mock.patch(
                "review.collect_project_commands", return_value=([], True, "")
            ), mock.patch("review.assert_clean_workspace"), mock.patch(
                "review.write_validation_evidence", return_value=root / "evidence.json"
            ), mock.patch(
                "review.run_simplifier_orchestrator",
                return_value=(result, root / "result.json"),
            ) as orchestrate:
                current, state, evidence = run_simplification_phase(
                    runner,
                    {"state_root": str(root / "state")},
                    "example",
                    project,
                    workspace,
                    pr,
                    run_dir,
                    apply=True,
                )
                self.assertEqual(current, pr)
                self.assertEqual(state["status"], "clean")
                self.assertEqual(evidence, root / "evidence.json")
                second, second_state, second_evidence = run_simplification_phase(
                    runner,
                    {"state_root": str(root / "state")},
                    "example",
                    project,
                    workspace,
                    pr,
                    run_dir,
                    apply=True,
                )
                self.assertEqual(second, pr)
                self.assertEqual(second_state, state)
                self.assertIsNone(second_evidence)
                orchestrate.assert_called_once()

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

        simplified_comment = format_review_comment(
            clean,
            original_head="b" * 40,
            final_head="b" * 40,
            validation_commands=[["pnpm", "run", "validate"]],
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
            validation_commands=[["pnpm", "run", "validate"]],
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

    def test_failed_validation_is_retained_as_repair_evidence(self) -> None:
        class FailingRunner:
            def run(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(command, 1, "type error at src/example.ts:7\n", "")

            def log(self, message: str) -> None:
                del message

        output, passed, failure = collect_project_commands(
            FailingRunner(),  # type: ignore[arg-type]
            Path("/tmp"),
            [["validate"]],
            [],
        )
        self.assertFalse(passed)
        self.assertIn("type error at src/example.ts:7", output)
        self.assertEqual(failure, "command failed (1): validate")

    def test_failed_repair_validation_is_corrected_then_revalidated(self) -> None:
        runner = mock.Mock()
        runner.log = mock.Mock()
        original = {"status": "repaired", "summary": "original"}
        corrected = {"status": "repaired", "summary": "corrected"}
        with (
            mock.patch("review.workspace_fingerprint", return_value={"test.ts": "hash"}),
            mock.patch("review.run_project_commands", return_value="setup passed"),
            mock.patch(
                "review.collect_project_commands",
                side_effect=[
                    ("type error", False, "command failed (1): validate"),
                    ("all passed", True, ""),
                ],
            ) as collect,
            mock.patch(
                "review.write_validation_evidence",
                side_effect=[Path("/tmp/validation-1.json"), Path("/tmp/validation-2.json")],
            ),
            mock.patch(
                "review.run_correction_orchestrator",
                return_value=(corrected, Path("/tmp/corrected-result.json")),
            ) as correct,
        ):
            result = validate_repair_with_corrections(
                runner,
                {"validation_correction_cycles": 2, "validation_attempts": 2},
                Path("/tmp/workspace"),
                {"baseRefOid": "a" * 40, "headRefOid": "b" * 40},
                Path("/tmp/run"),
                Path("/tmp/changed.txt"),
                Path("/tmp/docs.json"),
                Path("/tmp/skills.json"),
                [["setup"]],
                [["validate"]],
                [],
                {},
                original,
            )
        self.assertIs(result, corrected)
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(call.kwargs["attempts"] == 2 for call in collect.call_args_list))
        correct.assert_called_once()
        runner.log.assert_called_once_with(
            "Post-repair validation failed; starting focused correction cycle 1/2"
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

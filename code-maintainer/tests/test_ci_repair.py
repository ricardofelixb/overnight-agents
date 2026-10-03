from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("maintenance_ci_repair", SCRIPT_DIR / "ci_repair.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MaintenanceCiRepairTests(unittest.TestCase):
    def test_monorepo_requires_both_workflow_successes_on_the_same_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pending.json"
            path.write_text(json.dumps({
                "version": 1, "project": "agents-app", "pull_request": 17,
                "branch": "code-maintain/test", "head_sha": "a" * 40,
            }))
            project = {"name": "agents-app", "repository": "owner/agents-app",
                       "base_branch": "main", "maintenance_workflow_names": ["CI", "Runtime Quality"]}
            with (
                mock.patch.object(MODULE, "load_config", return_value={"projects": [project]}),
                mock.patch.object(MODULE, "_load_pending", side_effect=lambda _: (path, json.loads(path.read_text()))),
                mock.patch.object(MODULE, "_gh_json", return_value={
                    "state": "OPEN", "headRefOid": "a" * 40,
                    "headRefName": "code-maintain/test", "baseRefName": "main",
                }),
                mock.patch.object(MODULE.runtime, "resume_codex_session") as resume,
            ):
                MODULE.handle_ci_result(Path("config.json"), "agents-app", 17, 99,
                                        "a" * 40, "success", io.StringIO(), workflow_name="CI")
                self.assertEqual(json.loads(path.read_text())["ci_conclusion"], "pending")
                MODULE.handle_ci_result(Path("config.json"), "agents-app", 17, 100,
                                        "a" * 40, "success", io.StringIO(), workflow_name="Runtime Quality")
                self.assertEqual(json.loads(path.read_text())["ci_conclusion"], "success")
            resume.assert_not_called()

    def test_reconciler_waits_for_latest_complete_workflow_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state/pending/agents-app.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"project": "agents-app", "pull_request": 17,
                                       "head_sha": "a" * 40, "branch": "code-maintain/test",
                                       "codex_session_id": "thread-1"}))
            project = {"name": "agents-app", "repository": "owner/agents-app",
                       "maintenance_workflow_names": ["CI", "Runtime Quality"]}
            def run(name: str, identifier: int, status: str, conclusion: str | None) -> dict:
                return {"name": name, "id": identifier, "status": status,
                        "conclusion": conclusion, "event": "pull_request",
                        "head_sha": "a" * 40, "head_branch": "code-maintain/test"}
            with (
                mock.patch.object(MODULE, "SCRIPT_DIR", root),
                mock.patch.object(MODULE, "load_config", return_value={"projects": [project]}),
                mock.patch.object(MODULE, "_gh_json", return_value={"workflow_runs": [
                    run("CI", 99, "completed", "success"),
                    run("Runtime Quality", 100, "completed", "failure"),
                    run("Runtime Quality", 101, "in_progress", None),
                ]}),
                mock.patch.object(MODULE, "handle_ci_result", return_value="recorded") as handle,
            ):
                self.assertEqual(MODULE.reconcile(Path("config.json"), io.StringIO()), ["recorded"])
            self.assertEqual(handle.call_count, 1)
            self.assertEqual(handle.call_args.kwargs["workflow_name"], "CI")

    def test_success_records_exact_workflow_without_resuming_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pending_file = Path(temporary) / "pending.json"
            pending = {
                "version": 1,
                "project": "exac",
                "pull_request": 17,
                "branch": "code-maintain/test",
                "head_sha": "a" * 40,
                "codex_session_id": "thread-1",
            }
            project = {
                "repository": "owner/exac",
                "base_branch": "master",
            }
            with (
                mock.patch.object(MODULE, "load_config", return_value={}),
                mock.patch.object(MODULE, "_project", return_value=project),
                mock.patch.object(MODULE, "_load_pending", return_value=(pending_file, pending)),
                mock.patch.object(
                    MODULE,
                    "_gh_json",
                    return_value={
                        "state": "OPEN",
                        "headRefOid": "a" * 40,
                        "headRefName": "code-maintain/test",
                        "baseRefName": "master",
                    },
                ),
                mock.patch.object(MODULE, "atomic_json") as write,
                mock.patch.object(MODULE.runtime, "resume_codex_session") as resume,
            ):
                message = MODULE.handle_ci_result(
                    Path("config.json"), "exac", 17, 99, "a" * 40, "success", io.StringIO()
                )
            self.assertIn("CI passed", message)
            self.assertEqual(write.call_args.args[1]["ci_run_id"], 99)
            resume.assert_not_called()

    def test_stale_head_is_rejected_before_github_or_agent(self) -> None:
        pending = {
            "version": 1,
            "pull_request": 17,
            "branch": "code-maintain/test",
            "head_sha": "b" * 40,
        }
        with (
            mock.patch.object(MODULE, "load_config", return_value={}),
            mock.patch.object(MODULE, "_project", return_value={}),
            mock.patch.object(MODULE, "_load_pending", return_value=(Path("pending"), pending)),
            mock.patch.object(MODULE, "_gh_json") as github,
        ):
            with self.assertRaisesRegex(MODULE.MaintainerFailure, "stale"):
                MODULE.handle_ci_result(
                    Path("config.json"), "exac", 17, 99, "a" * 40, "failure", io.StringIO()
                )
        github.assert_not_called()


if __name__ == "__main__":
    unittest.main()

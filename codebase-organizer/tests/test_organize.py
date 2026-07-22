from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "controller.py"
SPEC = importlib.util.spec_from_file_location("codebase_organizer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OrganizerTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def valid_config(self) -> dict[str, object]:
        return {
            "version": 1,
            "enabled": True,
            "schedule": "30 2 * * *",
            "provider": "codex",
            "projects": [
                {
                    "name": "example",
                    "enabled": True,
                    "source_path": "/source",
                    "repository": "owner/example",
                    "base_branch": "main",
                    "environment_file": "/private/example.env.local",
                    "checklist_file": "/private/example.md",
                    "validation_commands": [["pnpm", "run", "validate"]],
                }
            ],
        }

    def test_selects_only_first_top_level_unchecked_item(self) -> None:
        text = """# Checklist

- [x] **done** — Finished
  - nested detail
- [ ] **sales** — Align sales
  - Backend: convex/receipts
  - Frontend: components/receipts
- [ ] **calendar** — Align calendar
"""
        item = MODULE.first_unchecked_item(text)
        self.assertIsNotNone(item)
        assert item
        self.assertEqual(item.item_id, "sales")
        self.assertIn("Frontend: components/receipts", item.block)
        self.assertNotIn("calendar", item.block)

    def test_exact_transition_accepts_one_marker_only(self) -> None:
        original = "- [ ] **sales** — Align sales\n  - Keep behavior\n"
        item = MODULE.first_unchecked_item(original)
        assert item
        completed = MODULE.completed_checklist_text(original, item)
        MODULE.require_exact_checklist_transition(original, completed, item)
        with self.assertRaisesRegex(MODULE.OrganizerFailure, "exactly"):
            MODULE.require_exact_checklist_transition(
                original, completed + "extra\n", item
            )

    def test_configuration_requires_boolean_switches_and_array_commands(self) -> None:
        config = self.valid_config()
        MODULE.validate_config(config)
        config["enabled"] = "true"
        with self.assertRaisesRegex(MODULE.OrganizerFailure, "boolean"):
            MODULE.validate_config(config)

    def test_linked_worktree_configuration_uses_repository_hooks(self) -> None:
        config = self.valid_config()
        project = config["projects"][0]
        del project["environment_file"]
        project["workspace"] = {
            "type": "linked-worktree",
            "setup_command": [
                "scripts/setup-worktree.sh",
                "--convex-mode",
                "local",
            ],
            "cleanup_command": ["scripts/cleanup-worktree.sh"],
            "management_token_file": "/private/convex-management.token",
        }
        MODULE.validate_config(config)

    def test_round_robin_skips_disabled_projects(self) -> None:
        config = self.valid_config()
        config["projects"] = [
            {**config["projects"][0], "name": "disabled", "enabled": False},
            {**config["projects"][0], "name": "enabled", "enabled": True},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "rotation"
            selected = MODULE.select_project(config, None, state)
        self.assertEqual(selected["name"], "enabled")

    def test_sensitive_paths_are_rejected(self) -> None:
        self.assertTrue(MODULE.sensitive_staged_path(".env.local"))
        self.assertTrue(MODULE.sensitive_staged_path("certificates/key.pem"))
        self.assertFalse(MODULE.sensitive_staged_path("src/sales/model.ts"))

    def test_restore_item_marker_changes_only_requested_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checklist = Path(temporary) / "organization.md"
            checklist.write_text(
                "- [x] **sales** — Sales\n- [x] **calendar** — Calendar\n"
            )
            MODULE.restore_item_marker(checklist, "sales")
            self.assertEqual(
                checklist.read_text(),
                "- [ ] **sales** — Sales\n- [x] **calendar** — Calendar\n",
            )

    def test_pending_pr_retries_workspace_cleanup_before_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending_path = root / "state" / "pending" / "example.json"
            pending_path.parent.mkdir(parents=True)
            pending_path.write_text(
                json.dumps(
                    {
                        "pull_request": 17,
                        "item_id": "sales",
                        "cleanup_workspace": "/private/organizer-worktree",
                    }
                )
            )
            checklist = root / "organization.md"
            checklist.write_text("- [x] **sales** — Sales\n")
            project = {
                "name": "example",
                "repository": "owner/example",
            }
            response = subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "state": "OPEN",
                        "mergedAt": None,
                        "url": "https://github.com/owner/example/pull/17",
                    }
                ),
                "",
            )

            with mock.patch.object(MODULE, "SCRIPT_DIR", root), mock.patch.object(
                MODULE, "cleanup_workspace"
            ) as cleanup, mock.patch.object(MODULE, "run", return_value=response):
                message = MODULE.reconcile_pending(project, checklist, mock.Mock())

            cleanup.assert_called_once_with(
                project, Path("/private/organizer-worktree"), mock.ANY
            )
            self.assertEqual(
                message,
                "example: waiting for organizer PR https://github.com/owner/example/pull/17",
            )
            self.assertNotIn(
                "cleanup_workspace", json.loads(pending_path.read_text())
            )

    def test_load_config_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps([]))
            with self.assertRaisesRegex(MODULE.OrganizerFailure, "JSON object"):
                MODULE.load_config(path)

    def test_controller_does_not_own_validation_or_correction_cycles(self) -> None:
        source = Path(MODULE.__file__).read_text()
        self.assertNotIn("validate_with_agent_corrections", source)
        self.assertNotIn("correction_prompt", source)
        prompt = MODULE.agent_prompt(
            workspace=Path("/tmp/workspace"),
            base_branch="main",
            branch="code-organize/sales",
            item=MODULE.ChecklistItem(0, "sales", "Sales", "- [ ] **sales** — Sales"),
            resuming=False,
            validation_commands=[["pnpm", "run", "validate"]],
        )
        self.assertIn("own the definitive validation", prompt)
        self.assertIn("fresh read-only verifier", prompt)

    def test_execute_project_publishes_one_validated_slice_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            seed = root / "seed"
            source = root / "source"
            environment = root / "private" / "example.env.local"
            checklist = root / "state" / "example.md"
            self.git("init", "--bare", str(origin))
            self.git("init", "-b", "main", str(seed))
            self.git("config", "user.email", "organizer@example.test", cwd=seed)
            self.git("config", "user.name", "Organizer Test", cwd=seed)
            (seed / ".gitignore").write_text(".env.local\n")
            (seed / "existing.txt").write_text("behavior\n")
            self.git("add", ".gitignore", "existing.txt", cwd=seed)
            self.git("commit", "-m", "initial", cwd=seed)
            self.git("remote", "add", "origin", str(origin), cwd=seed)
            self.git("push", "-u", "origin", "main", cwd=seed)
            self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
            self.git("clone", str(origin), str(source))
            environment.parent.mkdir()
            environment.write_text("EXAMPLE=value\n")
            environment.chmod(0o600)
            checklist.parent.mkdir()
            checklist.write_text(
                "# Checklist\n\n- [ ] **sales** — Align sales\n"
                "  - Preserve behavior.\n"
            )
            project = {
                "name": "example",
                "enabled": True,
                "source_path": str(source),
                "repository": "owner/example",
                "base_branch": "main",
                "environment_file": str(environment),
                "checklist_file": str(checklist),
                "validation_commands": [["true"]],
            }
            config = {
                "provider": "codex",
                "workspace_root": str(root / "workspaces"),
            }

            def fake_agent(
                _config: dict[str, object],
                workspace: Path,
                prompt: str,
                _stream: object,
            ) -> subprocess.CompletedProcess[str]:
                self.assertIn("MANUAL_UI_CHECKS_JSON", prompt)
                (workspace / "organized.txt").write_text("same behavior\n")
                checklist.write_text(
                    checklist.read_text().replace("- [ ] **sales**", "- [x] **sales**")
                )
                return subprocess.CompletedProcess(
                    [],
                    0,
                    'MANUAL_UI_CHECKS_JSON: ["Open sales and confirm navigation still works."]',
                    "",
                )

            original_run = MODULE.run
            created_body: dict[str, str] = {}

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[:3] == ["gh", "pr", "create"]:
                    created_body["value"] = command[command.index("--body") + 1]
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        "https://github.com/owner/example/pull/17\n",
                        "",
                    )
                return original_run(command, **kwargs)

            logs = root / "controller.log"
            fake_script_dir = root / "organizer"
            with logs.open("w") as stream, mock.patch.object(
                MODULE, "SCRIPT_DIR", fake_script_dir
            ), mock.patch.object(
                MODULE, "active_organizer_pr", return_value=None
            ), mock.patch.object(
                MODULE, "install_dependencies", return_value=None
            ), mock.patch.object(
                MODULE, "run_agent", side_effect=fake_agent
            ), mock.patch.object(MODULE, "run", side_effect=fake_run):
                message = MODULE.execute_project(
                    config, project, apply=True, stream=stream
                )

            self.assertEqual(
                message, "example: created https://github.com/owner/example/pull/17"
            )
            self.assertIn(
                "- [ ] Open sales and confirm navigation still works.",
                created_body["value"],
            )
            self.assertIn("- [x] **sales**", checklist.read_text())
            remote_branch = self.git(
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads/code-organize/",
                cwd=origin,
            )
            self.assertTrue(remote_branch.startswith("code-organize/sales-"))
            pending = json.loads(
                (fake_script_dir / "state" / "pending" / "example.json").read_text()
            )
            self.assertEqual(pending["pull_request"], 17)
            self.assertEqual(pending["item_id"], "sales")


if __name__ == "__main__":
    unittest.main()

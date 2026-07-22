from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
MODULE_PATH = SCRIPT_DIR / "controller.py"
SPEC = importlib.util.spec_from_file_location("scheduled_simplifier", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SimplifierControllerTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=cwd, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
        ).stdout.strip()

    def test_checklist_transition_is_exact(self) -> None:
        original = "# Simplify\n\n- [ ] src/auth/\n- [ ] src/billing/\n"
        item = MODULE.first_unchecked_item(original)
        assert item
        completed = MODULE.completed_checklist_text(original, item)
        MODULE.require_exact_transition(original, completed, item)
        self.assertIn("- [x] src/auth/", completed)
        with self.assertRaisesRegex(MODULE.SimplifierFailure, "exactly"):
            MODULE.require_exact_transition(original, completed + "extra\n", item)

    def test_controller_delegates_validation_without_correction_cycles(self) -> None:
        source = MODULE_PATH.read_text()
        self.assertNotIn("run_validation", source)
        self.assertNotIn("correction", source)
        prompt = MODULE.agent_prompt(
            Path("/tmp/workspace"),
            {"base_branch": "main", "validation_commands": [["pnpm", "run", "validate"]]},
            MODULE.ChecklistItem(0, "src/auth/", "- [ ] src/auth/"),
            "code-simplify/test",
            False,
        )
        self.assertIn("You own focused checks", prompt)
        self.assertIn("fresh verifier", prompt)

    def test_protected_publication_paths_are_rejected(self) -> None:
        self.assertEqual(
            MODULE.protected_paths(
                [
                    "src/a.ts",
                    "pnpm-lock.yaml",
                    ".github/workflows/ci.yml",
                    "scripts/__pycache__/tool.pyc",
                ]
            ),
            [
                "pnpm-lock.yaml",
                ".github/workflows/ci.yml",
                "scripts/__pycache__/tool.pyc",
            ],
        )

    def test_clone_workflow_publishes_one_agent_validated_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            seed = root / "seed"
            source = root / "source"
            environment = root / "private" / "example.env.local"
            checklist = root / "state" / "example.md"
            self.git("init", "--bare", str(origin))
            self.git("init", "-b", "main", str(seed))
            self.git("config", "user.email", "simplifier@example.test", cwd=seed)
            self.git("config", "user.name", "Simplifier Test", cwd=seed)
            (seed / ".gitignore").write_text(".env.local\nsimplification.md\n")
            (seed / "source.ts").write_text("export const value = 1;\n")
            self.git("add", ".", cwd=seed)
            self.git("commit", "-m", "initial", cwd=seed)
            self.git("remote", "add", "origin", str(origin), cwd=seed)
            self.git("push", "-u", "origin", "main", cwd=seed)
            self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
            self.git("clone", str(origin), str(source))
            environment.parent.mkdir()
            environment.write_text("EXAMPLE=value\n")
            environment.chmod(0o600)
            checklist.parent.mkdir()
            checklist.write_text("- [ ] src/\n")
            project = {
                "name": "example", "enabled": True,
                "source_path": str(source), "repository": "owner/example",
                "base_branch": "main", "environment_file": str(environment),
                "checklist_file": str(checklist),
                "validation_commands": [["true"]],
            }
            config = {"provider": "codex", "workspace_root": str(root / "workspaces")}

            def fake_agent(
                _config: dict[str, object], workspace: Path, prompt: str,
                _stream: object, **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                self.assertIn("MANUAL_UI_CHECKS_JSON", prompt)
                (workspace / "source.ts").write_text("export const value = (1);\n")
                local_checklist = workspace / "simplification.md"
                local_checklist.write_text(local_checklist.read_text().replace("[ ]", "[x]", 1))
                return subprocess.CompletedProcess(
                    [],
                    0,
                    'validated\nMANUAL_UI_CHECKS_JSON: ["Open settings and confirm the dialog appears."]',
                    "",
                )

            original_run = MODULE.runtime.run
            created_body: dict[str, str] = {}

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[:3] == ["gh", "pr", "list"]:
                    return subprocess.CompletedProcess(command, 0, "[]", "")
                if command[:3] == ["gh", "pr", "create"]:
                    created_body["value"] = command[command.index("--body") + 1]
                    return subprocess.CompletedProcess(
                        command, 0, "https://github.com/owner/example/pull/17\n", ""
                    )
                return original_run(command, **kwargs)

            fake_script_dir = root / "simplifier"
            environment_values = {
                "GIT_AUTHOR_NAME": "Simplifier Test",
                "GIT_AUTHOR_EMAIL": "simplifier@example.test",
                "GIT_COMMITTER_NAME": "Simplifier Test",
                "GIT_COMMITTER_EMAIL": "simplifier@example.test",
            }
            with mock.patch.object(MODULE, "SCRIPT_DIR", fake_script_dir), mock.patch.object(
                MODULE.runtime, "run_agent", side_effect=fake_agent
            ), mock.patch.object(MODULE.runtime, "run", side_effect=fake_run), mock.patch.dict(
                os.environ, environment_values
            ):
                with (root / "controller.log").open("w") as stream:
                    message = MODULE.execute_project(config, project, apply=True, stream=stream)

            self.assertEqual(message, "example: created https://github.com/owner/example/pull/17")
            self.assertIn(
                "- [ ] Open settings and confirm the dialog appears.",
                created_body["value"],
            )
            self.assertIn("[x]", checklist.read_text())
            self.assertTrue(
                self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/code-simplify/", cwd=origin)
            )
            pending = json.loads((fake_script_dir / "state/pending/example.json").read_text())
            self.assertEqual(pending["pull_request"], 17)


if __name__ == "__main__":
    unittest.main()

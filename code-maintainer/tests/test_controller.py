from __future__ import annotations

import importlib.util
import io
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
SPEC = importlib.util.spec_from_file_location("scheduled_maintainer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

from profiles import MaintenanceSlice, ProjectProfile


class MaintainerControllerTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def profile(self, root: Path) -> ProjectProfile:
        item = MaintenanceSlice(
            identifier="source",
            title="Source ownership",
            selectors=("source.ts",),
            search_terms=("source",),
            roles=(
                "reuse-simplification",
                "maintainability-organization",
                "efficiency-performance",
                "correctness-reliability",
                "security-hardening",
            ),
            guidance_domains=("react",),
        )
        manifest = root / "profile.json"
        slices = root / "slices.json"
        return ProjectProfile(
            name="example",
            root=root,
            manifest_path=manifest,
            shared_context=(),
            role_context={role: () for role in item.roles},
            slices_path=slices,
            slices=(item,),
        )

    def test_prompt_routes_every_selected_specialist_and_fresh_evidence(self) -> None:
        profile = self.profile(Path("/tmp/profile"))
        prompt = MODULE.agent_prompt(
            Path("/tmp/workspace"),
            {
                "name": "example",
                "base_branch": "main",
                "validation_commands": [["pnpm", "run", "validate"]],
            },
            profile,
            profile.slices[0],
            MODULE.CyclePosition(cycle=3, index=0),
            "code-maintain/test",
            Path("/tmp/evidence.json"),
            False,
        )
        self.assertIn("Maintenance cycle: 3", prompt)
        self.assertIn('"security-hardening"', prompt)
        self.assertIn("/tmp/evidence.json", prompt)
        self.assertIn("bounded concurrent batches", prompt)
        self.assertIn("fresh verifier", prompt)

    def test_project_name_cannot_escape_profile_directory(self) -> None:
        config = {
            "version": 2,
            "enabled": True,
            "schedule": "0 1 * * *",
            "context": {
                "skills_lock": "skills.json",
                "skill_release_root": "skills",
                "ai_files_root": "ai-files",
                "docs_catalog": "docs.json",
                "docs_refresh_script": "refresh.py",
                "docs_cache": "docs-cache",
            },
            "projects": [
                {
                    "name": "..",
                    "enabled": True,
                    "source_path": "/tmp/source",
                    "repository": "owner/repository",
                    "base_branch": "main",
                    "environment_file": "/tmp/project.env",
                    "validation_commands": [["true"]],
                }
            ],
        }
        with self.assertRaisesRegex(
            MODULE.MaintainerFailure, "name is missing or unsafe"
        ):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.json"
                path.write_text(json.dumps(config))
                MODULE.load_config(path)

    def test_protected_publication_paths_are_rejected(self) -> None:
        self.assertEqual(
            MODULE.protected_paths(
                [
                    "src/a.ts",
                    "pnpm-lock.yaml",
                    ".github/workflows/ci.yml",
                    "AGENTS.md",
                    ".agents/skills/example/SKILL.md",
                ]
            ),
            [
                "pnpm-lock.yaml",
                ".github/workflows/ci.yml",
                "AGENTS.md",
                ".agents/skills/example/SKILL.md",
            ],
        )

    def test_publication_requires_structured_report_before_git_mutation(self) -> None:
        item = self.profile(Path("/tmp/profile")).slices[0]

        with mock.patch.object(MODULE.runtime, "git") as mocked_git:
            with self.assertRaisesRegex(
                MODULE.MaintainerFailure,
                "exactly one MAINTENANCE_REPORT_JSON",
            ):
                MODULE.publish(
                    Path("/tmp/workspace"),
                    {},
                    {
                        "repository": "owner/example",
                        "base_branch": "main",
                        "validation_commands": [["true"]],
                    },
                    item,
                    MODULE.CyclePosition(cycle=1, index=0),
                    "code-maintain/test",
                    "unstructured report",
                    io.StringIO(),
                )

        mocked_git.assert_not_called()

    def test_unique_branch_avoids_local_and_remote_collisions(self) -> None:
        workspace = Path("/tmp/workspace")
        occupied = {
            "refs/remotes/origin/code-maintain/2026-07-22",
            "refs/heads/code-maintain/2026-07-22-193621",
            "refs/remotes/origin/code-maintain/2026-07-22-193621-2",
        }

        def fake_git(
            _workspace: Path,
            *arguments: str,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            reference = arguments[-1]
            return subprocess.CompletedProcess(
                ["git", *arguments],
                0 if reference in occupied else 1,
                "",
                "",
            )

        with mock.patch.object(
            MODULE.runtime, "git", side_effect=fake_git
        ), mock.patch.object(MODULE, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value.strftime.side_effect = [
                "2026-07-22",
                "193621",
            ]
            branch = MODULE.unique_branch(workspace)

        self.assertEqual(branch, "code-maintain/2026-07-22-193621-3")

    def test_clone_workflow_publishes_one_validated_semantic_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            seed = root / "seed"
            source = root / "source"
            environment = root / "private" / "example.env.local"
            self.git("init", "--bare", str(origin))
            self.git("init", "-b", "main", str(seed))
            self.git("config", "user.email", "maintainer@example.test", cwd=seed)
            self.git("config", "user.name", "Maintainer Test", cwd=seed)
            (seed / ".gitignore").write_text(".env.local\n")
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
            project = {
                "name": "example",
                "enabled": True,
                "source_path": str(source),
                "repository": "owner/example",
                "base_branch": "main",
                "environment_file": str(environment),
                "validation_commands": [["true"]],
            }
            fake_script_dir = root / "maintainer"
            config = {
                "provider": "codex",
                "_config_dir": str(fake_script_dir),
                "workspace_root": str(root / "workspaces"),
                "max_changed_files": 20,
                "max_diff_bytes": 100_000,
            }
            profile = self.profile(root / "profile")

            def fake_agent(
                _config: dict[str, object],
                workspace: Path,
                prompt: str,
                _stream: object,
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                self.assertIn("MANUAL_UI_CHECKS_JSON", prompt)
                self.assertIn("MAINTENANCE_REPORT_JSON", prompt)
                self.assertIn('"source"', prompt)
                (workspace / "source.ts").write_text("export const value = (1);\n")
                report = {
                    "summary": "Simplified source ownership without changing behavior.",
                    "role_outcomes": [
                        {
                            "role": role,
                            "status": (
                                "changed"
                                if role == "reuse-simplification"
                                else "no-change"
                            ),
                            "summary": f"Reviewed {role} and reconciled its findings.",
                        }
                        for role in profile.slices[0].roles
                    ],
                    "changes": [
                        {
                            "role": "reuse-simplification",
                            "summary": "Removed unnecessary source indirection.",
                        }
                    ],
                    "deferred": [
                        {
                            "role": "maintainability-organization",
                            "summary": "Deferred a rename without a canonical target.",
                        }
                    ],
                    "rejected": [
                        {
                            "role": "efficiency-performance",
                            "summary": "Rejected an optimization without measurable work.",
                        }
                    ],
                    "validation": ["The definitive validation command passed."],
                    "verifier": "PASS — the final diff is bounded.",
                }
                return subprocess.CompletedProcess(
                    [],
                    0,
                    (
                        "validated\n"
                        f"MAINTENANCE_REPORT_JSON: {json.dumps(report)}\n"
                        'MANUAL_UI_CHECKS_JSON: ["Open settings and confirm the dialog appears."]'
                    ),
                    "",
                )

            original_run = MODULE.runtime.run
            created_body: dict[str, str] = {}

            def fake_run(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                if command[:3] == ["gh", "pr", "list"]:
                    return subprocess.CompletedProcess(command, 0, "[]", "")
                if command[:3] == ["gh", "pr", "create"]:
                    created_body["value"] = command[command.index("--body") + 1]
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        "https://github.com/owner/example/pull/17\n",
                        "",
                    )
                return original_run(command, **kwargs)

            environment_values = {
                "GIT_AUTHOR_NAME": "Maintainer Test",
                "GIT_AUTHOR_EMAIL": "maintainer@example.test",
                "GIT_COMMITTER_NAME": "Maintainer Test",
                "GIT_COMMITTER_EMAIL": "maintainer@example.test",
            }
            evidence = root / "evidence.json"
            evidence.write_text("{}\n")
            with mock.patch.object(
                MODULE, "SCRIPT_DIR", fake_script_dir
            ), mock.patch.object(
                MODULE, "profile_for", return_value=profile
            ), mock.patch.object(
                MODULE, "prepare_context_evidence", return_value=evidence
            ), mock.patch.object(
                MODULE.runtime, "run_agent", side_effect=fake_agent
            ), mock.patch.object(
                MODULE.runtime, "run", side_effect=fake_run
            ), mock.patch.dict(
                os.environ, environment_values
            ):
                with (root / "controller.log").open("w") as stream:
                    message = MODULE.execute_project(
                        config, project, apply=True, stream=stream
                    )

            self.assertEqual(
                message, "example: created https://github.com/owner/example/pull/17"
            )
            self.assertIn("semantic slice `source`", created_body["value"])
            self.assertIn("## Specialist outcomes", created_body["value"])
            self.assertIn(
                "**Maintainability organization — No change:**",
                created_body["value"],
            )
            self.assertIn("## Changes made", created_body["value"])
            self.assertIn(
                "Removed unnecessary source indirection",
                created_body["value"],
            )
            self.assertIn("## Deferred findings", created_body["value"])
            self.assertIn("without a canonical target", created_body["value"])
            self.assertIn("## Rejected findings", created_body["value"])
            self.assertIn("without measurable work", created_body["value"])
            self.assertIn("## Reported validation", created_body["value"])
            self.assertIn("**Independent verifier:** PASS", created_body["value"])
            self.assertIn(
                "- [ ] Open settings and confirm the dialog appears.",
                created_body["value"],
            )
            self.assertTrue(
                self.git(
                    "for-each-ref",
                    "--format=%(refname:short)",
                    "refs/heads/code-maintain/",
                    cwd=origin,
                )
            )
            pending = json.loads(
                (fake_script_dir / "state/pending/example.json").read_text()
            )
            self.assertEqual(pending["slice"], "source")
            cycle = json.loads(
                (fake_script_dir / "state/cycles/example.json").read_text()
            )
            self.assertEqual(cycle["next_slice"], "source")


if __name__ == "__main__":
    unittest.main()

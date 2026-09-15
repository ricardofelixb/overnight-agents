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
from typing import Callable
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

    def test_disk_capacity_prunes_before_failing(self) -> None:
        stream = io.StringIO()
        with mock.patch.object(
            MODULE.shutil,
            "disk_usage",
            side_effect=[mock.Mock(free=2_000), mock.Mock(free=12_000)],
        ), mock.patch.object(MODULE.runtime, "run") as run:
            MODULE.ensure_disk_capacity({"minimum_free_bytes": 10_000}, stream)

        run.assert_called_once_with(["pnpm", "store", "prune"], stream=stream)
        self.assertIn("pruning the pnpm store", stream.getvalue())

    def test_disk_capacity_fails_early_when_prune_is_insufficient(self) -> None:
        with mock.patch.object(
            MODULE.shutil,
            "disk_usage",
            side_effect=[mock.Mock(free=2_000), mock.Mock(free=3_000)],
        ), mock.patch.object(MODULE.runtime, "run"):
            with self.assertRaisesRegex(
                MODULE.MaintainerFailure, "insufficient disk before maintenance"
            ):
                MODULE.ensure_disk_capacity(
                    {"minimum_free_bytes": 10_000}, io.StringIO()
                )

    def profile(self, root: Path) -> ProjectProfile:
        item = MaintenanceSlice(
            identifier="source",
            title="Source ownership",
            selectors=("source.ts",),
            search_terms=("source",),
            roles=(
                "reuse-simplification",
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
        self.assertIn("Do not run tests", prompt)
        self.assertIn("pull-request checks own validation", prompt)
        self.assertIn("Size objective: leave this slice smaller", prompt)
        self.assertIn("sizing.py /tmp/workspace --budget 30", prompt)

    def test_prompt_states_the_configured_growth_budget(self) -> None:
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
            budget=12,
        )
        self.assertIn("--budget 12", prompt)
        self.assertIn("An oversized tree is discarded unpublished", prompt)

    def test_resume_prompt_skips_completed_specialists_and_validation(self) -> None:
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
            True,
        )
        self.assertIn("Do not rerun specialists or make new edits", prompt)
        self.assertIn("run only `git diff --check`", prompt)

    def test_enabled_slice_filters_runtime_disabled_roles(self) -> None:
        item = self.profile(Path("/tmp/profile")).slices[0]

        selected = MODULE.enabled_slice(
            {
                "agents": {
                    "security-hardening": False,
                }
            },
            item,
        )

        self.assertEqual(
            selected.roles,
            (
                "reuse-simplification",
                "efficiency-performance",
                "correctness-reliability",
            ),
        )
        self.assertEqual(selected.selectors, item.selectors)
        self.assertEqual(selected.guidance_domains, item.guidance_domains)

    def test_enabled_slice_rejects_a_slice_with_no_enabled_roles(self) -> None:
        item = self.profile(Path("/tmp/profile")).slices[0]

        with self.assertRaisesRegex(
            MODULE.MaintainerFailure, "has no enabled specialist roles"
        ):
            MODULE.enabled_slice(
                {"agents": {role: False for role in item.roles}}, item
            )

    def test_config_rejects_unknown_non_boolean_and_all_disabled_agents(self) -> None:
        base = {
            "version": 3,
            "enabled": True,
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
                    "name": "example",
                    "enabled": True,
                    "schedule": "0 13 * * *",
                    "source_path": "/tmp/source",
                    "repository": "owner/repository",
                    "base_branch": "main",
                    "environment_file": "/tmp/project.env",
                    "validation_commands": [["true"]],
                }
            ],
        }
        invalid_agents = (
            ({"unknown-role": True}, "unknown roles"),
            ({"reuse-simplification": "yes"}, "must be a boolean"),
            (
                {
                    role: False
                    for role in self.profile(Path("/tmp/profile")).slices[0].roles
                },
                "enable at least one",
            ),
        )

        for agents, message in invalid_agents:
            with self.subTest(
                agents=agents
            ), tempfile.TemporaryDirectory() as temporary:
                config = dict(base, agents=agents)
                path = Path(temporary) / "config.json"
                path.write_text(json.dumps(config))
                with self.assertRaisesRegex(MODULE.MaintainerFailure, message):
                    MODULE.load_config(path)

    def test_config_rejects_root_schedule_and_overlapping_project_schedules(self) -> None:
        base = {
            "version": 3,
            "enabled": True,
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
                    "name": "exac",
                    "enabled": True,
                    "schedule": "0 13 * * *",
                    "source_path": "/tmp/exac",
                    "repository": "owner/exac",
                    "base_branch": "master",
                    "environment_file": "/tmp/exac.env",
                    "validation_commands": [["true"]],
                },
                {
                    "name": "agents",
                    "enabled": True,
                    "schedule": "0 18 * * *",
                    "source_path": "/tmp/agents",
                    "repository": "owner/agents",
                    "base_branch": "main",
                    "environment_file": "/tmp/agents.env",
                    "validation_commands": [["true"]],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(dict(base, schedule="0 1 * * *")))
            with self.assertRaisesRegex(
                MODULE.MaintainerFailure, "schedule belongs on each project"
            ):
                MODULE.load_config(path)
            overlapping = json.loads(json.dumps(base))
            overlapping["projects"][1]["schedule"] = "0 13 * * *"
            path.write_text(json.dumps(overlapping))
            with self.assertRaisesRegex(MODULE.MaintainerFailure, "schedule overlaps"):
                MODULE.load_config(path)

    def test_config_rejects_invalid_slice_repair(self) -> None:
        base = {
            "version": 3,
            "enabled": True,
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
                    "name": "example",
                    "enabled": True,
                    "schedule": "0 13 * * *",
                    "source_path": "/tmp/source",
                    "repository": "owner/repository",
                    "base_branch": "main",
                    "environment_file": "/tmp/project.env",
                    "validation_commands": [["true"]],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(dict(base, slice_repair={"provider": "claude"})))
            with self.assertRaisesRegex(
                MODULE.MaintainerFailure, "slice_repair provider must be codex"
            ):
                MODULE.load_config(path)

    def test_apply_repairs_stale_selectors_then_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "source.ts").write_text("export const value = 1;\n")
            stale = self.profile(root / "profile")
            stale = stale.__class__(
                name=stale.name,
                root=stale.root,
                manifest_path=stale.manifest_path,
                shared_context=stale.shared_context,
                role_context=stale.role_context,
                slices_path=stale.slices_path,
                slices=(
                    stale.slices[0].__class__(
                        identifier="source",
                        title="Source ownership",
                        selectors=("missing.ts",),
                        search_terms=stale.slices[0].search_terms,
                        roles=stale.slices[0].roles,
                        guidance_domains=stale.slices[0].guidance_domains,
                    ),
                ),
            )
            fixed = self.profile(root / "profile")
            profiles = iter([stale, fixed])
            repaired: list[tuple[tuple[str, str], ...]] = []
            fake_script_dir = root / "maintainer"
            (fake_script_dir / "state/pending").mkdir(parents=True)
            (fake_script_dir / "state/cycles").mkdir(parents=True)
            project = {
                "name": "example",
                "enabled": True,
                "source_path": str(root / "source"),
                "repository": "owner/example",
                "base_branch": "main",
                "environment_file": str(root / "example.env"),
                "validation_commands": [["true"]],
            }
            config = {
                "provider": "codex",
                "_config_dir": str(fake_script_dir),
                "workspace_root": str(root / "workspaces"),
            }

            def fake_repair(*args: object, **_kwargs: object) -> None:
                repaired.append(args[4])  # type: ignore[arg-type]

            with mock.patch.object(
                MODULE, "SCRIPT_DIR", fake_script_dir
            ), mock.patch.object(
                MODULE, "profile_for", side_effect=lambda _project: next(profiles)
            ), mock.patch.object(
                MODULE, "prepare_workspace",
                return_value={
                    "workspace": str(workspace),
                    "resuming": False,
                    "branch": "",
                    "created": True,
                },
            ), mock.patch.object(
                MODULE, "repair_stale_slice_registry", side_effect=fake_repair
            ), mock.patch.object(
                MODULE, "active_maintainer_pr", return_value="https://github.com/owner/example/pull/9"
            ), mock.patch.object(
                MODULE, "reconcile_pending", return_value=None
            ):
                with (root / "controller.log").open("w") as stream:
                    message = MODULE.execute_project(
                        config, project, apply=True, stream=stream
                    )

            self.assertEqual(repaired, [(("source", "missing.ts"),)])
            self.assertEqual(
                message, "example: waiting for maintainer PR https://github.com/owner/example/pull/9"
            )

    def test_dry_run_does_not_repair_stale_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            stale = self.profile(root / "profile")
            stale = stale.__class__(
                name=stale.name,
                root=stale.root,
                manifest_path=stale.manifest_path,
                shared_context=stale.shared_context,
                role_context=stale.role_context,
                slices_path=stale.slices_path,
                slices=(
                    stale.slices[0].__class__(
                        identifier="source",
                        title="Source ownership",
                        selectors=("missing.ts",),
                        search_terms=stale.slices[0].search_terms,
                        roles=stale.slices[0].roles,
                        guidance_domains=stale.slices[0].guidance_domains,
                    ),
                ),
            )
            fake_script_dir = root / "maintainer"
            (fake_script_dir / "state/pending").mkdir(parents=True)
            project = {
                "name": "example",
                "enabled": True,
                "source_path": str(root / "source"),
                "repository": "owner/example",
                "base_branch": "main",
                "environment_file": str(root / "example.env"),
                "validation_commands": [["true"]],
            }
            with mock.patch.object(
                MODULE, "SCRIPT_DIR", fake_script_dir
            ), mock.patch.object(
                MODULE, "profile_for", return_value=stale
            ), mock.patch.object(
                MODULE, "prepare_workspace",
                return_value={
                    "workspace": str(workspace),
                    "resuming": False,
                    "branch": "",
                    "created": True,
                },
            ), mock.patch.object(
                MODULE, "repair_stale_slice_registry"
            ) as repair, mock.patch.object(
                MODULE, "reconcile_pending", return_value=None
            ):
                with (root / "controller.log").open("w") as stream:
                    with self.assertRaisesRegex(
                        MODULE.ProfileFailure, "missing.ts"
                    ):
                        MODULE.execute_project(
                            {}, project, apply=False, stream=stream
                        )
            repair.assert_not_called()

    def test_project_context_overlays_shared_defaults(self) -> None:
        base = {
            "version": 3,
            "enabled": True,
            "context": {
                "skills_lock": "skills.json",
                "skill_release_root": "skills",
                "ai_files_root": "ai-files",
                "docs_catalog": "docs.json",
                "docs_refresh_script": "refresh.py",
                "docs_cache": "docs-cache",
                "docs_max_age_hours": 24,
            },
            "projects": [
                {
                    "name": "exac",
                    "enabled": True,
                    "schedule": "0 13 * * *",
                    "source_path": "/tmp/exac",
                    "repository": "owner/exac",
                    "base_branch": "master",
                    "environment_file": "/tmp/exac.env",
                    "validation_commands": [["true"]],
                },
                {
                    "name": "agents",
                    "enabled": True,
                    "schedule": "0 18 * * *",
                    "source_path": "/tmp/agents",
                    "repository": "owner/agents",
                    "base_branch": "main",
                    "environment_file": "/tmp/agents.env",
                    "validation_commands": [["true"]],
                    "context": {
                        "docs_catalog": "agents-docs.json",
                        "docs_max_age_hours": 48,
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(base))
            config = MODULE.load_config(path)
        self.assertEqual(
            MODULE.resolve_context(config, config["projects"][0])["docs_catalog"],
            "docs.json",
        )
        overlay = MODULE.resolve_context(config, config["projects"][1])
        self.assertEqual(overlay["docs_catalog"], "agents-docs.json")
        self.assertEqual(overlay["docs_max_age_hours"], 48)
        self.assertEqual(overlay["skills_lock"], "skills.json")
        opted_out = json.loads(json.dumps(base))
        opted_out["projects"][1]["context"] = False
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(opted_out))
            config = MODULE.load_config(path)
        self.assertIsNone(MODULE.resolve_context(config, config["projects"][1]))
        invalid = json.loads(json.dumps(base))
        invalid["projects"][1]["context"] = {"unknown": "nope"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(invalid))
            with self.assertRaisesRegex(
                MODULE.MaintainerFailure, "unknown fields: unknown"
            ):
                MODULE.load_config(path)

    def test_project_name_cannot_escape_profile_directory(self) -> None:
        config = {
            "version": 3,
            "enabled": True,
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
                    "schedule": "0 13 * * *",
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
                "MAINTENANCE_REPORT_JSON",
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

    def test_publication_refuses_source_growth_without_a_fix(self) -> None:
        item = self.profile(Path("/tmp/profile")).slices[0]
        report = {
            "summary": "Added a helper.",
            "role_outcomes": [
                {
                    "role": role,
                    "status": "changed" if role == "reuse-simplification" else "no-change",
                    "summary": f"Reviewed {role}.",
                }
                for role in item.roles
            ],
            "changes": [{"role": "reuse-simplification", "summary": "Added a helper."}],
            "deferred": [],
            "rejected": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self.git("init", "-b", "main", str(workspace))
            self.git("config", "user.email", "maintainer@example.test", cwd=workspace)
            self.git("config", "user.name", "Maintainer Test", cwd=workspace)
            (workspace / "source.ts").write_text("export const value = 1;\n")
            self.git("add", ".", cwd=workspace)
            self.git("commit", "-m", "initial", cwd=workspace)
            (workspace / "source.ts").write_text("export const value = 1;\n" * 10)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "source.test.ts").write_text("test\n" * 50)

            with self.assertRaises(MODULE.OversizedChange) as raised:
                MODULE.publish(
                    workspace,
                    {},
                    {
                        "repository": "owner/example",
                        "base_branch": "main",
                        "validation_commands": [["true"]],
                    },
                    item,
                    MODULE.CyclePosition(cycle=1, index=0),
                    "code-maintain/test",
                    f"MAINTENANCE_REPORT_JSON: {json.dumps(report)}",
                    io.StringIO(),
                )
            commits = self.git("rev-list", "--count", "HEAD", cwd=workspace)

        self.assertIn("grew by 9 lines without an adopted", str(raised.exception))
        self.assertEqual(raised.exception.size.source.net, 9)
        self.assertEqual(raised.exception.size.test.added, 50)
        self.assertEqual(commits, "1")

    def test_config_bounds_the_source_growth_budget(self) -> None:
        config = json.loads((SCRIPT_DIR / "config.example.json").read_text())
        self.assertEqual(MODULE.growth_budget(config), 30)
        self.assertEqual(MODULE.growth_budget({}), 30)
        for value in (-1, 1_001, "30", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    MODULE.ConfigurationFailure, "max_source_growth_lines"
                ):
                    MODULE.validate_config(dict(config, max_source_growth_lines=value))

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

    def run_clone_workflow(
        self,
        root: Path,
        *,
        edit: Callable[[Path], None],
        changes: list[dict[str, str]],
    ) -> tuple[str, dict[str, str], Path, Path]:
        """Run one clone-workspace lifecycle against a temporary origin."""

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
        changed_roles = {change["role"] for change in changes}

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
            self.assertIn(f"sizing.py {workspace} --budget 30", prompt)
            edit(workspace)
            report = {
                "summary": "Reconciled the source slice.",
                "role_outcomes": [
                    {
                        "role": role,
                        "status": "changed" if role in changed_roles else "no-change",
                        "summary": f"Reviewed {role} and reconciled its findings.",
                    }
                    for role in profile.slices[0].roles
                ],
                "changes": changes,
                "deferred": [
                    {
                        "role": "correctness-reliability",
                        "summary": "Deferred a rename without a canonical target.",
                    }
                ],
                "rejected": [
                    {
                        "role": "efficiency-performance",
                        "summary": "Rejected an optimization without measurable work.",
                    }
                ],
            }
            message = (
                f"MAINTENANCE_REPORT_JSON: {json.dumps(report)}\n"
                'MANUAL_UI_CHECKS_JSON: ["Open settings and confirm the dialog appears."]'
            )
            return subprocess.CompletedProcess(
                [],
                0,
                "\n".join(
                    [
                        '{"type":"thread.started","thread_id":"thread-test"}',
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "agent_message",
                                    "text": message,
                                },
                            }
                        ),
                    ]
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
        return message, created_body, fake_script_dir, origin

    def test_clone_workflow_publishes_one_validated_semantic_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            message, created_body, fake_script_dir, origin = self.run_clone_workflow(
                root,
                edit=lambda workspace: (workspace / "source.ts").write_text(
                    "export const value = (1);\n"
                ),
                changes=[
                    {
                        "role": "reuse-simplification",
                        "summary": "Removed unnecessary source indirection.",
                    }
                ],
            )

            self.assertEqual(
                message,
                "example: created https://github.com/owner/example/pull/17 "
                "(source +0, tests +0, docs +0)",
            )
            self.assertIn("semantic slice `source`", created_body["value"])
            self.assertIn("## Specialist outcomes", created_body["value"])
            self.assertIn(
                "**Correctness reliability — No change:**",
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
            self.assertIn("## Size", created_body["value"])
            self.assertIn(
                "- **Source:** +1 / \u22121 lines (net +0)", created_body["value"]
            )
            self.assertIn("## Pull-request validation", created_body["value"])
            self.assertIn("did not run these commands locally", created_body["value"])
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
            self.assertEqual(pending["codex_session_id"], "thread-test")
            cycle = json.loads(
                (fake_script_dir / "state/cycles/example.json").read_text()
            )
            self.assertEqual(cycle["next_slice"], "source")

    def test_clone_workflow_discards_growth_without_a_fix_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def grow(workspace: Path) -> None:
                (workspace / "source.ts").write_text(
                    "export const value = 1;\n" + "export const extra = 1;\n" * 5
                )
                (workspace / "source.test.ts").write_text("test\n" * 40)

            message, created_body, fake_script_dir, origin = self.run_clone_workflow(
                root,
                edit=grow,
                changes=[
                    {
                        "role": "efficiency-performance",
                        "summary": "Added a memoized helper.",
                    }
                ],
            )

            self.assertEqual(
                message,
                "example: cycle 1 source discarded unpublished: production source "
                "grew by 5 lines without an adopted correctness or security fix; "
                "simplification must not grow the source "
                "(source +5, tests +40, docs +0)",
            )
            self.assertEqual(created_body, {})
            self.assertEqual(
                self.git("for-each-ref", "refs/heads/code-maintain/", cwd=origin),
                "",
            )
            self.assertFalse((fake_script_dir / "state/pending/example.json").exists())
            cycle = json.loads(
                (fake_script_dir / "state/cycles/example.json").read_text()
            )
            self.assertEqual(cycle["cycle"], 2)
            self.assertEqual(cycle["last_completed"]["outcome"], "discarded-growth")
            workspace = root / "workspaces" / "example"
            self.assertEqual(self.git("status", "--porcelain", cwd=workspace), "")
            self.assertEqual(
                (workspace / "source.ts").read_text(), "export const value = 1;\n"
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import slice_repair
from profiles import MaintenanceSlice, ProjectProfile
from slice_repair import (
    SliceRepairFailure,
    allowed_repair_relatives,
    repair_stale_slice_registry,
    slice_repair_prompt,
)


class SliceRepairTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def profile(self, slices_path: Path) -> ProjectProfile:
        item = MaintenanceSlice(
            identifier="source",
            title="Source ownership",
            selectors=("missing.ts",),
            search_terms=("source",),
            roles=(
                "reuse-simplification",
                "maintainability-organization",
                "efficiency-performance",
                "correctness-reliability",
                "security-hardening",
            ),
            guidance_domains=(),
        )
        return ProjectProfile(
            name="example",
            root=slices_path.parent,
            manifest_path=slices_path.parent / "profile.json",
            shared_context=(),
            role_context={role: () for role in item.roles},
            slices_path=slices_path,
            slices=(item,),
        )

    def test_prompt_names_missing_selectors_and_forbids_git_publish(self) -> None:
        slices = (
            slice_repair.REPO_ROOT
            / "code-maintainer/skills/code-maintainer/references/projects/example/slices.json"
        )
        prompt = slice_repair_prompt(
            {
                "name": "example",
                "repository": "owner/example",
                "base_branch": "main",
            },
            self.profile(slices),
            Path("/tmp/product"),
            (("source", "missing.ts"),),
        )
        self.assertIn("`source`: `missing.ts`", prompt)
        self.assertNotIn("gpt-5.6-luna", prompt)
        self.assertIn("Do not `git add`, `git commit`, `git push`", prompt)
        self.assertIn(
            "code-maintainer/skills/code-maintainer/references/projects/example/slices.json",
            prompt,
        )
        self.assertIn("/tmp/product", prompt)

    def test_repair_commits_and_pushes_after_codex_edits_allowed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            origin = root / "origin.git"
            repo = root / "overnight-agents"
            maintainer = repo / "code-maintainer"
            slices = (
                maintainer
                / "skills/code-maintainer/references/projects/example/slices.json"
            )
            tests = maintainer / "tests/test_profiles.py"
            workspace = root / "product"
            workspace.mkdir()
            (workspace / "source.ts").write_text("export const value = 1;\n")
            self.git("init", "--bare", str(origin), cwd=root)
            self.git("init", "-b", "main", str(repo), cwd=root)
            self.git("config", "user.email", "maintainer@example.test", cwd=repo)
            self.git("config", "user.name", "Maintainer Test", cwd=repo)
            slices.parent.mkdir(parents=True)
            tests.parent.mkdir(parents=True)
            slices.write_text(json.dumps({"version": 1, "slices": []}))
            tests.write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
            self.git("add", ".", cwd=repo)
            self.git("commit", "-m", "initial", cwd=repo)
            self.git("remote", "add", "origin", str(origin), cwd=repo)
            self.git("push", "-u", "origin", "main", cwd=repo)
            profile = self.profile(slices)
            original_run = slice_repair.runtime.run

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[:2] == ["codex", "exec"]:
                    slices.write_text(
                        json.dumps(
                            {
                                "version": 1,
                                "defaults": {
                                    "roles": list(profile.slices[0].roles),
                                    "guidance_domains": [],
                                },
                                "slices": [
                                    {
                                        "id": "source",
                                        "title": "Source ownership",
                                        "selectors": ["source.ts"],
                                    }
                                ],
                            }
                        )
                    )
                    return subprocess.CompletedProcess(command, 0, "repaired\n", "")
                if command[:3] == [sys.executable, "-m", "unittest"]:
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")
                return original_run(command, **kwargs)

            repaired_profile = self.profile(slices)
            repaired_profile = ProjectProfile(
                name=profile.name,
                root=profile.root,
                manifest_path=profile.manifest_path,
                shared_context=(),
                role_context=profile.role_context,
                slices_path=slices,
                slices=(
                    MaintenanceSlice(
                        identifier="source",
                        title="Source ownership",
                        selectors=("source.ts",),
                        search_terms=("source",),
                        roles=profile.slices[0].roles,
                        guidance_domains=(),
                    ),
                ),
            )
            stream = io.StringIO()
            with mock.patch.object(slice_repair, "REPO_ROOT", repo), mock.patch.object(
                slice_repair, "MAINTAINER_DIR", maintainer
            ), mock.patch.object(
                slice_repair.runtime, "run", side_effect=fake_run
            ), mock.patch.object(
                slice_repair, "load_project_profile", return_value=repaired_profile
            ), mock.patch.dict(
                "os.environ",
                {
                    "GIT_AUTHOR_NAME": "Maintainer Test",
                    "GIT_AUTHOR_EMAIL": "maintainer@example.test",
                    "GIT_COMMITTER_NAME": "Maintainer Test",
                    "GIT_COMMITTER_EMAIL": "maintainer@example.test",
                },
            ):
                repair_stale_slice_registry(
                    {
                        "slice_repair": {
                            "codex_model": "gpt-5.6-luna",
                            "codex_reasoning_effort": "medium",
                        }
                    },
                    {
                        "name": "example",
                        "repository": "owner/example",
                        "base_branch": "main",
                    },
                    profile,
                    workspace,
                    (("source", "missing.ts"),),
                    stream,
                )

            log = stream.getvalue()
            self.assertIn("gpt-5.6-luna medium", log)
            self.assertEqual(
                self.git("log", "-1", "--pretty=%s", cwd=repo),
                "Fix example maintenance slice selectors to match the current repository.",
            )
            self.assertEqual(
                self.git("rev-parse", "HEAD", cwd=repo),
                self.git("rev-parse", "origin/main", cwd=repo),
            )
            self.assertIn("source.ts", slices.read_text())

    def test_repair_refuses_a_dirty_overnight_agents_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "overnight-agents"
            repo.mkdir()
            self.git("init", "-b", "main", str(repo), cwd=Path(temporary))
            self.git("config", "user.email", "maintainer@example.test", cwd=repo)
            self.git("config", "user.name", "Maintainer Test", cwd=repo)
            (repo / "README.md").write_text("dirty\n")
            slices = repo / "code-maintainer/skills/code-maintainer/references/projects/example/slices.json"
            profile = self.profile(slices)
            with mock.patch.object(slice_repair, "REPO_ROOT", repo), self.assertRaisesRegex(
                SliceRepairFailure, "working tree is dirty"
            ):
                repair_stale_slice_registry(
                    {},
                    {"name": "example", "repository": "owner/example", "base_branch": "main"},
                    profile,
                    Path(temporary),
                    (("source", "missing.ts"),),
                    io.StringIO(),
                )

    def test_disabled_repair_keeps_the_stale_selector_block(self) -> None:
        from profiles import ProfileFailure

        with tempfile.TemporaryDirectory() as temporary:
            slices = Path(temporary) / "slices.json"
            profile = self.profile(slices)
            with self.assertRaisesRegex(ProfileFailure, "missing.ts"):
                repair_stale_slice_registry(
                    {"slice_repair": {"enabled": False}},
                    {
                        "name": "example",
                        "repository": "owner/example",
                        "base_branch": "main",
                    },
                    profile,
                    Path(temporary),
                    (("source", "missing.ts"),),
                    io.StringIO(),
                )
        relatives = allowed_repair_relatives("agents")
        self.assertIn(
            "code-maintainer/skills/code-maintainer/references/projects/agents/slices.json",
            relatives,
        )
        self.assertIn("code-maintainer/tests/test_profiles.py", relatives)
        self.assertNotIn(
            "code-maintainer/skills/code-maintainer/references/projects/exac/slices.json",
            relatives,
        )


if __name__ == "__main__":
    unittest.main()

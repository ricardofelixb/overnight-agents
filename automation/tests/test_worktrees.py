from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "worktrees.py"
SPEC = importlib.util.spec_from_file_location("shared_worktrees", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LinkedWorktreeTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        origin = root / "origin.git"
        seed = root / "seed"
        source = root / "source"
        checklist = root / "state" / "organization.md"
        self.git("init", "--bare", str(origin))
        self.git("init", "-b", "main", str(seed))
        self.git("config", "user.email", "worktrees@example.test", cwd=seed)
        self.git("config", "user.name", "Worktree Test", cwd=seed)
        (seed / "scripts").mkdir()
        setup = seed / "scripts" / "setup-worktree.sh"
        setup.write_text("#!/bin/sh\nset -eu\nprintf setup > .setup-ran\n")
        setup.chmod(0o755)
        cleanup = seed / "scripts" / "cleanup-worktree.sh"
        cleanup.write_text(
            "#!/bin/sh\nset -eu\n"
            "printf '%s' \"${CONVEX_MANAGEMENT_TOKEN:-missing}\" "
            "> ../cleanup-token\n"
        )
        cleanup.chmod(0o755)
        (seed / ".gitignore").write_text(".setup-ran\n")
        (seed / "example.txt").write_text("safe\n")
        self.git("add", ".", cwd=seed)
        self.git("commit", "-m", "initial", cwd=seed)
        self.git("remote", "add", "origin", str(origin), cwd=seed)
        self.git("push", "-u", "origin", "main", cwd=seed)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
        self.git("clone", str(origin), str(source))
        checklist.parent.mkdir()
        checklist.write_text("- [ ] **example** — Example\n")
        return source, checklist, origin

    def prepare(self, root: Path, source: Path, checklist: Path) -> dict[str, str | bool]:
        return MODULE.prepare_linked_worktree(
            source_path=source,
            workspace_root=root / "workspaces",
            project_name="example",
            base_branch="main",
            branch_prefix="code-organize",
            checklist_file=checklist,
            checklist_name="organization.md",
            automation_label="organizer",
        )

    def test_prepares_real_worktree_without_copying_dirty_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, checklist, _ = self.fixture(root)
            (source / "example.txt").write_text("user work\n")

            result = self.prepare(root, source, checklist)
            workspace = Path(str(result["workspace"]))

            self.assertTrue((workspace / ".git").is_file())
            self.assertEqual((workspace / "example.txt").read_text(), "safe\n")
            self.assertEqual(
                (workspace / "organization.md").resolve(), checklist.resolve()
            )
            self.assertEqual(
                Path(
                    self.git("rev-parse", "--git-common-dir", cwd=workspace)
                ).resolve(),
                (source / ".git").resolve(),
            )

    def test_dirty_automation_branch_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, checklist, _ = self.fixture(root)
            first = self.prepare(root, source, checklist)
            workspace = Path(str(first["workspace"]))
            self.git("checkout", "-b", "code-organize/interrupted", cwd=workspace)
            (workspace / "example.txt").write_text("unfinished\n")

            resumed = self.prepare(root, source, checklist)

            self.assertTrue(resumed["resuming"])
            self.assertEqual(resumed["branch"], "code-organize/interrupted")
            self.assertEqual((workspace / "example.txt").read_text(), "unfinished\n")

    def test_setup_and_cleanup_hooks_use_controller_only_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, checklist, _ = self.fixture(root)
            prepared = self.prepare(root, source, checklist)
            workspace = Path(str(prepared["workspace"]))
            MODULE.run_setup_hook(workspace, ["scripts/setup-worktree.sh"])
            self.assertEqual((workspace / ".setup-ran").read_text(), "setup")
            self.git("checkout", "-b", "code-organize/example", cwd=workspace)
            token = root / "private" / "convex-management.token"
            token.parent.mkdir()
            token.write_text("secret-token\n")
            token.chmod(0o600)

            MODULE.cleanup_linked_worktree(
                source_path=source,
                workspace=workspace,
                branch_prefix="code-organize",
                cleanup_command=["scripts/cleanup-worktree.sh"],
                management_token_file=token,
            )

            self.assertFalse(workspace.exists())
            self.assertEqual(
                (root / "workspaces" / "cleanup-token").read_text(),
                "secret-token",
            )
            self.assertEqual(
                self.git("branch", "--list", "code-organize/example", cwd=source),
                "",
            )

    def test_hook_can_be_resolved_from_trusted_checkout_for_an_exact_head_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, origin = self.fixture(root)
            workspace = root / "review-clone"
            self.git("clone", str(origin), str(workspace))
            untrusted_hook = workspace / "scripts" / "setup-worktree.sh"
            untrusted_hook.write_text("#!/bin/sh\nexit 99\n")
            untrusted_hook.chmod(0o755)

            MODULE.run_setup_hook(
                workspace,
                ["scripts/setup-worktree.sh"],
                hook_root=source,
            )

            self.assertEqual((workspace / ".setup-ran").read_text(), "setup")

    def test_management_token_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            token.write_text("secret-token\n")
            token.chmod(0o644)
            with self.assertRaisesRegex(MODULE.WorktreeFailure, "group or other"):
                MODULE.read_management_token(token)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from workspace import WorkspaceFailure, prepare_workspace


class WorkspaceTests(unittest.TestCase):
    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()

    def fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
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
        (seed / "example.txt").write_text("safe\n")
        self.git("add", ".gitignore", "example.txt", cwd=seed)
        self.git("commit", "-m", "initial", cwd=seed)
        self.git("remote", "add", "origin", str(origin), cwd=seed)
        self.git("push", "-u", "origin", "main", cwd=seed)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
        self.git("clone", str(origin), str(source))
        environment.parent.mkdir()
        environment.write_text("EXAMPLE=value\n")
        environment.chmod(0o600)
        checklist.parent.mkdir()
        checklist.write_text("- [ ] example\n")
        return source, environment, checklist, origin

    def prepare(self, root: Path, source: Path, environment: Path, checklist: Path) -> dict[str, str | bool]:
        return prepare_workspace(
            source_path=source,
            workspace_root=root / "workspaces",
            project_name="example",
            base_branch="main",
            branch_prefix="code-simplify",
            environment_file=environment,
            checklist_file=checklist,
        )

    def test_dirty_source_checkout_does_not_contaminate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, environment, checklist, _ = self.fixture(root)
            (source / "example.txt").write_text("user work\n")
            result = self.prepare(root, source, environment, checklist)
            workspace = Path(str(result["workspace"]))
            self.assertEqual((workspace / "example.txt").read_text(), "safe\n")
            self.assertEqual(self.git("status", "--porcelain", cwd=workspace), "")
            self.assertEqual((workspace / ".env.local").resolve(), environment.resolve())
            self.assertEqual((workspace / "simplification.md").resolve(), checklist.resolve())

    def test_dirty_simplifier_branch_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, environment, checklist, _ = self.fixture(root)
            first = self.prepare(root, source, environment, checklist)
            workspace = Path(str(first["workspace"]))
            self.git("checkout", "-b", "code-simplify/interrupted", cwd=workspace)
            (workspace / "example.txt").write_text("unfinished\n")
            resumed = self.prepare(root, source, environment, checklist)
            self.assertTrue(resumed["resuming"])
            self.assertEqual(resumed["branch"], "code-simplify/interrupted")
            self.assertEqual((workspace / "example.txt").read_text(), "unfinished\n")

    def test_environment_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, environment, checklist, _ = self.fixture(root)
            environment.chmod(0o644)
            with self.assertRaisesRegex(WorkspaceFailure, "group or other"):
                self.prepare(root, source, environment, checklist)

    def test_unmanaged_workspace_env_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, environment, checklist, _ = self.fixture(root)
            first = self.prepare(root, source, environment, checklist)
            workspace = Path(str(first["workspace"]))
            (workspace / ".env.local").unlink()
            (workspace / ".env.local").write_text("unexpected=value\n")
            with self.assertRaisesRegex(WorkspaceFailure, "not a controller-managed symlink"):
                self.prepare(root, source, environment, checklist)


if __name__ == "__main__":
    unittest.main()

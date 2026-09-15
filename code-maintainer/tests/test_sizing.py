from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from sizing import (  # noqa: E402
    Delta,
    DiffSize,
    brief,
    classify,
    growth_violation,
    measure_staged,
    size_section,
)


def git(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    ).stdout.strip()


def repository(root: Path, files: dict[str, str]) -> Path:
    git("init", "-b", "main", str(root), cwd=root)
    git("config", "user.email", "sizing@example.test", cwd=root)
    git("config", "user.name", "Sizing Test", cwd=root)
    for name, content in files.items():
        (root / name).write_text(content)
    git("add", "-A", cwd=root)
    git("commit", "-m", "initial", cwd=root)
    return root


class SizingTests(unittest.TestCase):
    def test_classifies_tests_and_docs_by_path_shape(self) -> None:
        cases = {
            "src/a.ts": "source",
            "convex/publicPortals/queries.ts": "source",
            "src/components/docs/DocsPage.tsx": "source",
            "src/testing/fixtures.ts": "source",
            "tests/portal/collections.test.ts": "test",
            "src/__tests__/a.ts": "test",
            "src/Button.spec.tsx": "test",
            "core/test_runtime.py": "test",
            "companies/exac/agents/collection/tests/conftest.py": "test",
            "README.md": "docs",
            "docs/guide.mdx": "docs",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify(path), expected)

    def test_numstat_totals_each_category_and_skips_binary_files(self) -> None:
        size = DiffSize.from_numstat(
            "3\t10\tsrc/a.ts\n40\t0\ttests/a.test.ts\n-\t-\timage.png\n2\t0\tREADME.md\n"
        )
        self.assertEqual(size.source, Delta(3, 10))
        self.assertEqual(size.test, Delta(40, 0))
        self.assertEqual(size.docs, Delta(2, 0))
        self.assertEqual(size.source.net, -7)

    def test_source_may_grow_only_for_a_fix_within_budget(self) -> None:
        shrink = DiffSize(Delta(1, 10), Delta(50, 0), Delta(3, 0))
        self.assertIsNone(growth_violation(shrink, fix_adopted=False, budget=0))
        grow = DiffSize(Delta(12, 2), Delta(0, 0), Delta(0, 0))
        self.assertIn(
            "grew by 10 lines without an adopted",
            growth_violation(grow, fix_adopted=False, budget=30) or "",
        )
        self.assertIsNone(growth_violation(grow, fix_adopted=True, budget=30))
        self.assertIn(
            "above the 5-line budget",
            growth_violation(grow, fix_adopted=True, budget=5) or "",
        )

    def test_measures_the_staged_tree_including_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = repository(
                Path(temporary),
                {"source.ts": "a\nb\nc\n", "README.md": "# Title\n"},
            )
            (repo / "source.ts").write_text("a\n")
            (repo / "extra.ts").write_text("x\ny\n")
            (repo / "tests").mkdir()
            (repo / "tests" / "source.test.ts").write_text("1\n2\n3\n4\n")
            (repo / "README.md").unlink()
            git("add", "-A", cwd=repo)

            size = measure_staged(repo)

        self.assertEqual(size.source, Delta(2, 2))
        self.assertEqual(size.test, Delta(4, 0))
        self.assertEqual(size.docs, Delta(0, 1))

    def test_cli_measures_the_working_tree_and_reports_the_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = repository(Path(temporary), {"source.ts": "a\n"})
            (repo / "source.ts").write_text("a\nb\nc\nd\ne\n")
            command = [sys.executable, str(SCRIPT_DIR / "sizing.py"), str(repo)]

            rejected = subprocess.run(
                command + ["--budget", "3"], capture_output=True, text=True
            )
            over_budget = subprocess.run(
                command + ["--budget", "3", "--fix-adopted"],
                capture_output=True,
                text=True,
            )
            accepted = subprocess.run(
                command + ["--budget", "10", "--fix-adopted"],
                capture_output=True,
                text=True,
            )
            staged = git("diff", "--cached", "--name-only", cwd=repo)
            status = git("status", "--porcelain", cwd=repo)

        self.assertEqual(rejected.returncode, 1, rejected.stdout)
        self.assertIn("REJECT: production source grew by 4 lines", rejected.stdout)
        self.assertEqual(over_budget.returncode, 1, over_budget.stdout)
        self.assertIn("above the 3-line budget", over_budget.stdout)
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        self.assertIn("source: +4 / −0 lines (net +4)", accepted.stdout)
        self.assertIn("OK:", accepted.stdout)
        self.assertEqual(staged, "", "the CLI measures without leaving the tree staged")
        self.assertEqual(status, "M source.ts")

    def test_renders_the_brief_and_pull_request_section(self) -> None:
        size = DiffSize(Delta(1, 10), Delta(5, 0), Delta(0, 0))
        self.assertEqual(brief(size), "source -9, tests +5, docs +0")
        section = size_section(size)
        self.assertTrue(section.startswith("## Size\n\n"))
        self.assertIn("- **Source:** +1 / −10 lines (net -9)", section)
        self.assertIn("- **Tests:** +5 / −0 lines (net +5)", section)


if __name__ == "__main__":
    unittest.main()

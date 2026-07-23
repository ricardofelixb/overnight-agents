from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from automation import checklists


class ChecklistTests(unittest.TestCase):
    def test_marks_only_the_selected_marker_and_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checklist.md"
            original = "- [ ] first\n- [ ] second\n"
            path.write_text(original)
            path.chmod(0o600)

            completed = checklists.mark_completed(path, original, 1)

            self.assertEqual(completed, "- [ ] first\n- [x] second\n")
            self.assertEqual(path.read_text(), completed)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_rejects_agent_changes_and_invalid_marker(self) -> None:
        with self.assertRaisesRegex(checklists.ChecklistFailure, "must not modify"):
            checklists.require_unchanged("- [ ] item\n", "- [x] item\n")
        with self.assertRaisesRegex(checklists.ChecklistFailure, "marker"):
            checklists.completed_text("- [x] item\n", 0)

    def test_updates_symlink_target_without_replacing_the_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state" / "checklist.md"
            target.parent.mkdir()
            target.write_text("- [ ] item\n")
            link = root / "workspace-checklist.md"
            link.symlink_to(target)

            checklists.mark_completed(link, target.read_text(), 0)

            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), "- [x] item\n")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cycles import (
    CyclePosition,
    advance,
    checkpoint,
    load_position,
    reconcile_registry,
    retire_removed_slice,
)


class MaintenanceCycleTests(unittest.TestCase):
    def test_cycle_wraps_forever_and_records_last_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycle.json"
            identifiers = ("alpha", "beta")
            first = load_position(path, identifiers)
            checkpoint(path, first, identifiers)
            second = advance(
                path,
                first,
                identifiers,
                slice_id="alpha",
                outcome="audited-no-change",
            )
            wrapped = advance(
                path,
                second,
                identifiers,
                slice_id="beta",
                outcome="merged",
            )
            self.assertEqual(first, CyclePosition(cycle=1, index=0))
            self.assertEqual(second, CyclePosition(cycle=1, index=1))
            self.assertEqual(wrapped, CyclePosition(cycle=2, index=0))
            state = json.loads(path.read_text())
            self.assertEqual(state["next_slice"], "alpha")
            self.assertEqual(state["last_completed"]["outcome"], "merged")

    def test_semantic_position_survives_registry_reordering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycle.json"
            path.write_text(
                json.dumps({"version": 1, "cycle": 4, "next_slice": "beta"})
            )
            position = load_position(path, ("beta", "alpha"))
            self.assertEqual(position, CyclePosition(cycle=4, index=0))

    def test_removed_position_moves_to_next_surviving_snapshot_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycle.json"
            path.write_text(
                json.dumps(
                    {"version": 1, "cycle": 4, "next_slice": "removed"}
                )
            )

            position = reconcile_registry(
                path,
                ("alpha", "charlie"),
                previous_slice_ids=("alpha", "removed", "bravo", "charlie"),
            )

            self.assertEqual(position, CyclePosition(cycle=4, index=1))
            self.assertEqual(json.loads(path.read_text())["next_slice"], "charlie")

    def test_legacy_removed_position_uses_last_completed_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycle.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cycle": 4,
                        "next_slice": "removed",
                        "last_completed": {"cycle": 4, "slice": "alpha"},
                    }
                )
            )

            position = reconcile_registry(path, ("alpha", "bravo"))

            self.assertEqual(position, CyclePosition(cycle=4, index=1))
            self.assertEqual(json.loads(path.read_text())["next_slice"], "bravo")

    def test_terminal_removed_slice_is_recorded_and_retired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cycle.json"
            path.write_text(
                json.dumps(
                    {"version": 1, "cycle": 4, "next_slice": "removed"}
                )
            )

            position = retire_removed_slice(
                path,
                ("alpha", "charlie"),
                cycle=4,
                slice_id="removed",
                outcome="merged",
                previous_slice_ids=("alpha", "removed", "charlie"),
            )

            state = json.loads(path.read_text())
            self.assertEqual(position, CyclePosition(cycle=4, index=1))
            self.assertEqual(state["next_slice"], "charlie")
            self.assertEqual(state["last_completed"]["slice"], "removed")
            self.assertEqual(state["last_completed"]["outcome"], "merged")


if __name__ == "__main__":
    unittest.main()

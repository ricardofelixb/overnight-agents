from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cycles import CyclePosition, advance, checkpoint, load_position


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


if __name__ == "__main__":
    unittest.main()

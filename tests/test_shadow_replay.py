from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from execution.shadow_replay import run_shadow_replay
from journal.shadow_review import review_shadow_day
from datetime import date


class ShadowReplayTests(unittest.TestCase):
    def test_complete_replay_reaches_simulated_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_shadow_replay(
                "config/shadow_replay.example.json", log_root=directory
            )
            self.assertEqual("COMPLETE", result["session_state"])
            self.assertFalse(result["strategy_evidence_eligible"])
            review = review_shadow_day(date(2026, 7, 17), root=directory)
            self.assertEqual(1, review.simulated_fills)
            self.assertEqual(1, review.simulated_exits)


if __name__ == "__main__":
    unittest.main()

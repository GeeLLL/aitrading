from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from execution.shadow_pilot import run_one_shot_pilot


class ShadowPilotTests(unittest.TestCase):
    def test_one_shot_pilot_is_completed_without_assumed_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_one_shot_pilot(
                "config/shadow_input.example.json", log_root=Path(directory)
            )
            self.assertEqual("PILOT_SIMULATED_ENTRY", result["status"])
            self.assertEqual("COMPLETE", result["session_state"])
            self.assertFalse(result["assumed_fill"])
            self.assertTrue(result["simulation_only"])
            self.assertTrue(Path(result["log_path"]).exists())


if __name__ == "__main__":
    unittest.main()

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

    def test_formal_mode_requires_authorization_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "FORMAL_SHADOW_REQUIRES_AUTHORIZATION"):
                run_one_shot_pilot(
                    "config/shadow_input.example.json",
                    log_root=Path(directory),
                    pilot_mode=False,
                )

    def test_authorized_formal_mode_produces_non_pilot_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_one_shot_pilot(
                "config/shadow_input.example.json",
                log_root=Path(directory),
                pilot_mode=False,
                shadow_authorized=True,
            )
            self.assertEqual("PILOT_SIMULATED_ENTRY", result["status"])
            self.assertNotIn("PILOT_NOT_STRATEGY_EVIDENCE", result["reasons"])


if __name__ == "__main__":
    unittest.main()

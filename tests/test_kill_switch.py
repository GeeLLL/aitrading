from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monitoring.kill_switch import AutomationHalt, KillSwitch


class KillSwitchTests(unittest.TestCase):
    def test_missing_marker_means_emergency_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "trading_armed"
            status = KillSwitch(marker).status()
            self.assertTrue(status.engaged)
            self.assertEqual("TRADING_ARM_MARKER_ABSENT", status.reason)

    def test_engage_removes_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "trading_armed"
            marker.touch()
            switch = KillSwitch(marker)
            self.assertFalse(switch.status().engaged)

            status = switch.engage()
            self.assertTrue(status.engaged)
            self.assertFalse(marker.exists())


class AutomationHaltTests(unittest.TestCase):
    def test_absent_marker_means_automation_may_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            halt = AutomationHalt(Path(directory) / "automation_halt.json")
            self.assertFalse(halt.active())

    def test_engage_creates_durable_marker_with_no_clear_method(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            halt = AutomationHalt(Path(directory) / "automation_halt.json")
            path = halt.engage()
            self.assertTrue(halt.active())
            self.assertTrue(path.exists())
            self.assertFalse(hasattr(halt, "clear"))


if __name__ == "__main__":
    unittest.main()

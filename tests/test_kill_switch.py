from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monitoring.kill_switch import KillSwitch


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


if __name__ == "__main__":
    unittest.main()

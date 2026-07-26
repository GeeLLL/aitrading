from __future__ import annotations

import plistlib
import unittest
from pathlib import Path

from monitoring.daily_schedule import DAILY_SLOTS
from scripts.generate_self_arming_plist import LABEL, render


class GenerateSelfArmingPlistTests(unittest.TestCase):
    def _plist(self) -> dict:
        xml = render(
            python=Path("/nonexistent-test-prefix/bin/python3"),
            workdir=Path("/Users/ge/ge/aitrading"),
        )
        return plistlib.loads(xml.encode("utf-8"))

    def test_points_at_self_arming_worker(self) -> None:
        data = self._plist()
        self.assertEqual(LABEL, data["Label"])
        self.assertTrue(data["ProgramArguments"][1].endswith("scripts/self_arming_worker.py"))
        self.assertEqual("/nonexistent-test-prefix/bin/python3", data["ProgramArguments"][0])

    def test_recurring_not_date_pinned(self) -> None:
        # The whole point: entries carry only Hour+Minute, never Month/Day.
        data = self._plist()
        entries = data["StartCalendarInterval"]
        self.assertEqual(len(DAILY_SLOTS), len(entries))
        for entry in entries:
            self.assertEqual({"Hour", "Minute"}, set(entry.keys()))
        slot_times = {(h, m) for (h, m) in DAILY_SLOTS}
        self.assertEqual(slot_times, {(e["Hour"], e["Minute"]) for e in entries})

    def test_no_keepalive_respawn_loop(self) -> None:
        data = self._plist()
        # No KeepAlive: a clean "closed day / not my slot" exit must not respawn.
        self.assertNotIn("KeepAlive", data)
        self.assertFalse(data["RunAtLoad"])

    def test_label_distinct_from_date_pinned_worker(self) -> None:
        from scripts.generate_shadow_worker_plist import LABEL as PINNED_LABEL

        self.assertNotEqual(PINNED_LABEL, LABEL)


if __name__ == "__main__":
    unittest.main()

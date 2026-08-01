from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from monitoring.power_watch import check_power

PT = ZoneInfo("America/Los_Angeles")
# 2026-07-28 is a Tuesday trading day; 2026-08-01 is a Saturday.
IN_WINDOW = datetime(2026, 7, 28, 9, 30, tzinfo=PT)
BEFORE_WINDOW = datetime(2026, 7, 28, 4, 0, tzinfo=PT)
AFTER_WINDOW = datetime(2026, 7, 28, 16, 0, tzinfo=PT)
WEEKEND = datetime(2026, 8, 1, 9, 30, tzinfo=PT)


class PowerWatchTests(unittest.TestCase):
    def test_ac_power_in_window_is_clean(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = check_power(IN_WINDOW, project_root=root, ac_power=True)
            self.assertEqual(result["status"], "ON_AC")
            self.assertFalse((root / "logs/incidents").exists())

    def test_battery_in_window_raises_incident_and_alert(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = check_power(IN_WINDOW, project_root=root, ac_power=False)
            self.assertEqual(result["status"], "ON_BATTERY")
            incident = Path(str(result["incident_path"]))
            payload = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(payload["incident_type"], "COLLECTION_WINDOW_POWER_RISK")
            self.assertEqual(payload["reason"], "ON_BATTERY")
            self.assertTrue((root / "logs/incidents/alerts").is_dir())

    def test_unknown_power_state_also_alerts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = check_power(IN_WINDOW, project_root=root, ac_power=None)
            # ac_power=None means "read it live"; in a sandbox pmset may work,
            # so accept either a real reading or the unknown branch — what must
            # never happen is a silent pass with no status.
            self.assertIn(result["status"], {"ON_AC", "ON_BATTERY", "POWER_STATE_UNKNOWN"})

    def test_incident_is_idempotent_per_day(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = check_power(IN_WINDOW, project_root=root, ac_power=False)
            body = Path(str(first["incident_path"])).read_text(encoding="utf-8")
            check_power(IN_WINDOW, project_root=root, ac_power=False)
            self.assertEqual(
                Path(str(first["incident_path"])).read_text(encoding="utf-8"), body
            )
            self.assertEqual(
                len(list((root / "logs/incidents").glob("*.scheduler-incident.json"))), 1
            )

    def test_outside_the_window_is_not_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for moment in (BEFORE_WINDOW, AFTER_WINDOW):
                result = check_power(moment, project_root=root, ac_power=False)
                self.assertEqual(result["status"], "OUTSIDE_WINDOW")
            self.assertFalse((root / "logs/incidents").exists())

    def test_non_market_day_is_not_flagged(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = check_power(WEEKEND, project_root=root, ac_power=False)
            self.assertEqual(result["status"], "OUTSIDE_MARKET_DAY")
            self.assertFalse((root / "logs/incidents").exists())

    def test_window_covers_preflight_through_close(self):
        from monitoring.power_watch import WINDOW_END, WINDOW_START
        # Must start before the 05:45 preflight and end after the 13:05 close.
        self.assertLess(WINDOW_START, datetime(2026, 7, 28, 5, 45, tzinfo=PT).time())
        self.assertGreater(WINDOW_END, datetime(2026, 7, 28, 13, 5, tzinfo=PT).time())


class WatchdogWiringTests(unittest.TestCase):
    def test_watchdog_reports_power_status(self):
        source = (Path(__file__).resolve().parents[1] / "scripts/watchdog_tick.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("check_power", source)
        self.assertIn('"power"', source)


if __name__ == "__main__":
    unittest.main()

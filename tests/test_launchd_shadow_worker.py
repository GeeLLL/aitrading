from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.launchd_shadow_worker import _resolve_slot, _run_id


LOCAL = ZoneInfo("America/Los_Angeles")


class LaunchdShadowWorkerTests(unittest.TestCase):
    def test_old_canary_is_not_a_production_slot(self):
        now = datetime(2026, 7, 20, 16, 35, 10, tzinfo=LOCAL)
        with self.assertRaisesRegex(ValueError, "NO_REGISTERED_SLOT"):
            _resolve_slot(now)

    def test_resolves_exact_market_gate(self):
        now = datetime(2026, 7, 21, 6, 35, 20, tzinfo=LOCAL)
        scheduled, kind, symbol = _resolve_slot(now)
        self.assertEqual((kind, symbol), ("MARKET_GATE", "SPY"))
        self.assertEqual(_run_id(scheduled, kind), "market-gate-20260721-0635")

    def test_resolves_preopen_canary(self):
        now = datetime(2026, 7, 21, 6, 10, 15, tzinfo=LOCAL)
        scheduled, kind, symbol = _resolve_slot(now)
        self.assertEqual((kind, symbol), ("CANARY", "SPY"))
        self.assertEqual(_run_id(scheduled, kind), "launchd-canary-20260721-0610")

    def test_resolves_pilot_slot_with_small_launch_delay(self):
        now = datetime(2026, 7, 21, 8, 24, 10, tzinfo=LOCAL)
        scheduled, kind, symbol = _resolve_slot(now)
        self.assertEqual((kind, symbol), ("PILOT_SAMPLE", "NVDA"))
        self.assertEqual(_run_id(scheduled, kind), "pilot-20260721-0823")

    def test_rejects_unscheduled_invocation(self):
        now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=LOCAL)
        with self.assertRaisesRegex(ValueError, "NO_REGISTERED_SLOT"):
            _resolve_slot(now)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from monitoring.daily_schedule import DAILY_SLOTS, expected_runs_for_date
from scripts.launchd_shadow_worker import _log_root, _resolve_slot, _run_id


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

    def test_log_root_follows_the_observation_date(self):
        self.assertTrue(
            str(_log_root(datetime(2026, 7, 22, 6, 10, tzinfo=LOCAL))).endswith(
                "logs/launchd_worker/2026-07-22"
            )
        )

    def test_slot_hint_resolves_exact_slot(self):
        # The self-arming wrapper passes ROBINHOOD_SLOT_HHMM to name the slot.
        now = datetime(2026, 7, 21, 7, 3, 5, tzinfo=LOCAL)
        scheduled, kind, symbol = _resolve_slot(now, "0703")
        self.assertEqual((kind, symbol), ("PILOT_SAMPLE", "SPY"))
        self.assertEqual(_run_id(scheduled, kind), "pilot-20260721-0703")

    def test_slot_hint_still_enforces_freshness_guard(self):
        # A slot hint can disambiguate but can NEVER backfill: a fire that lands
        # far from the named slot (e.g. launchd replaying after a wake) is refused.
        stale = datetime(2026, 7, 21, 9, 3, 0, tzinfo=LOCAL)
        with self.assertRaisesRegex(ValueError, "SLOT_FIRED_OUTSIDE_180_SECONDS"):
            _resolve_slot(stale, "0703")

    def test_unknown_slot_hint_is_rejected(self):
        now = datetime(2026, 7, 21, 7, 4, 0, tzinfo=LOCAL)
        with self.assertRaisesRegex(ValueError, "UNKNOWN_SLOT_HHMM"):
            _resolve_slot(now, "0704")


class DailyScheduleTests(unittest.TestCase):
    def test_full_day_expectation_table(self):
        runs = expected_runs_for_date(date(2026, 7, 22))
        self.assertEqual(len(DAILY_SLOTS), len(runs))
        self.assertEqual(17, len(runs))
        run_ids = [run_id for run_id, _scheduled in runs]
        self.assertEqual("launchd-canary-20260722-0610", run_ids[0])
        self.assertEqual("market-gate-20260722-0635", run_ids[1])
        self.assertEqual("pilot-20260722-0703", run_ids[2])
        self.assertEqual("pilot-close-canary-20260722-1305", run_ids[-1])
        for _run_id_value, scheduled in runs:
            self.assertIsNotNone(scheduled.tzinfo)


if __name__ == "__main__":
    unittest.main()

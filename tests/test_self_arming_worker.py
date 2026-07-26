from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring.daily_schedule import expected_runs_for_date
from scripts.self_arming_worker import (
    SLOT_MATCH_WINDOW_SECONDS,
    plan_fire,
    register_todays_expectations,
)

PT = ZoneInfo("America/Los_Angeles")

# 2026-07-22 is a Wednesday (a regular trading day); 2026-07-25 is a Saturday;
# 2026-04-03 is Good Friday; 2026-11-27 is the (early-close) day after Thanksgiving.
TRADING_DAY = datetime(2026, 7, 22, 6, 10, tzinfo=PT)


class PlanFireTests(unittest.TestCase):
    def test_exact_slot_fires(self) -> None:
        decision = plan_fire(datetime(2026, 7, 22, 6, 10, tzinfo=PT))
        self.assertTrue(decision.run)
        self.assertEqual("0610", decision.slot_hhmm)
        self.assertEqual("CANARY", decision.kind)
        self.assertEqual("SPY", decision.symbol)

    def test_pilot_slot_fires(self) -> None:
        decision = plan_fire(datetime(2026, 7, 22, 7, 3, tzinfo=PT))
        self.assertTrue(decision.run)
        self.assertEqual("0703", decision.slot_hhmm)
        self.assertEqual("PILOT_SAMPLE", decision.kind)

    def test_weekend_is_noop(self) -> None:
        decision = plan_fire(datetime(2026, 7, 25, 7, 3, tzinfo=PT))
        self.assertFalse(decision.run)
        self.assertEqual("NON_MARKET_DAY", decision.reason)

    def test_holiday_is_noop(self) -> None:
        # Good Friday 2026.
        decision = plan_fire(datetime(2026, 4, 3, 7, 3, tzinfo=PT))
        self.assertFalse(decision.run)
        self.assertEqual("NON_MARKET_DAY", decision.reason)

    def test_off_slot_market_day_is_noop(self) -> None:
        # 05:00 PT is far from any registered slot.
        decision = plan_fire(datetime(2026, 7, 22, 5, 0, tzinfo=PT))
        self.assertFalse(decision.run)
        self.assertEqual("NO_SLOT_WINDOW", decision.reason)

    def test_within_match_window_still_fires(self) -> None:
        # A launch up to ~4 minutes late (< SLOT_MATCH_WINDOW_SECONDS) still
        # counts as its slot; the real worker's tighter 180s guard is separate.
        self.assertGreaterEqual(SLOT_MATCH_WINDOW_SECONDS, 240)
        late = datetime(2026, 7, 22, 7, 7, 0, tzinfo=PT)  # 240s after the 07:03 slot
        decision = plan_fire(late)
        self.assertTrue(decision.run)
        self.assertEqual("0703", decision.slot_hhmm)

    def test_early_close_afternoon_is_noop(self) -> None:
        # Day after Thanksgiving 2026 closes at 1pm ET (10:00 PT); an 11:03 PT
        # fire is after the close and must no-op.
        decision = plan_fire(datetime(2026, 11, 27, 11, 3, tzinfo=PT))
        self.assertFalse(decision.run)
        self.assertEqual("AFTER_EARLY_CLOSE", decision.reason)

    def test_early_close_morning_still_fires(self) -> None:
        decision = plan_fire(datetime(2026, 11, 27, 9, 3, tzinfo=PT))
        self.assertTrue(decision.run)

    def test_market_gate_uses_session_date_not_exchange_date(self) -> None:
        # Late Friday evening PT is already Saturday in New York. The gate must
        # use the session (PT) date the slots and registration use, so a Friday
        # 23:30 PT fire is a market day (just no slot), NOT wrongly NON_MARKET_DAY.
        friday_late = datetime(2026, 7, 24, 23, 30, tzinfo=PT)  # Fri in PT, Sat in NY
        decision = plan_fire(friday_late)
        self.assertFalse(decision.run)
        self.assertEqual("NO_SLOT_WINDOW", decision.reason)  # not NON_MARKET_DAY
        # And a genuine PT-date holiday is still correctly closed.
        self.assertEqual("NON_MARKET_DAY", plan_fire(datetime(2026, 4, 3, 7, 3, tzinfo=PT)).reason)


class RegisterExpectationsTests(unittest.TestCase):
    def test_registers_full_day_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = expected_runs_for_date(TRADING_DAY.date())
            count = register_todays_expectations(TRADING_DAY, directory=directory)
            self.assertEqual(len(expected), count)
            files = sorted(Path(directory).glob("*.expected.json"))
            self.assertEqual(len(expected), len(files))
            # Registration writes a valid, timezone-aware EXPECTED envelope.
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual("EXPECTED", payload["status"])
            self.assertEqual(1, payload["schema_version"])
            # Re-running does not duplicate: same run_ids, same file count.
            again = register_todays_expectations(TRADING_DAY, directory=directory)
            self.assertEqual(count, again)
            self.assertEqual(len(files), len(sorted(Path(directory).glob("*.expected.json"))))

    def test_registered_run_ids_match_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            register_todays_expectations(TRADING_DAY, directory=directory)
            names = {p.name for p in Path(directory).glob("*.expected.json")}
            for run_id, _scheduled in expected_runs_for_date(TRADING_DAY.date()):
                self.assertIn(f"{run_id}.expected.json", names)


if __name__ == "__main__":
    unittest.main()

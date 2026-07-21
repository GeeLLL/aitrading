from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from monitoring.session_schedule import InstrumentSession, completed_bar_scan_times, evaluate_instrument_session


ET = ZoneInfo("America/New_York")


class SessionScheduleTests(unittest.TestCase):
    def test_unknown_instrument_session_fails_closed(self) -> None:
        decision = evaluate_instrument_session(
            None, now=datetime(2026, 7, 17, 10, 0, tzinfo=ET),
            entry_delay_minutes=30, stop_before_close_minutes=90,
            force_exit_before_close_minutes=30, emergency_exit_before_close_minutes=15,
        )
        self.assertFalse(decision.market_open)
        self.assertIn("OFFICIAL_SESSION_UNKNOWN", decision.reasons)

    def test_instrument_specific_close_controls_windows(self) -> None:
        session = InstrumentSession(
            "SPY", datetime(2026, 7, 17, 9, 30, tzinfo=ET),
            datetime(2026, 7, 17, 16, 15, tzinfo=ET), "ROBINHOOD_OFFICIAL_MCP",
        )
        decision = evaluate_instrument_session(
            session, now=datetime(2026, 7, 17, 15, 50, tzinfo=ET),
            entry_delay_minutes=30, stop_before_close_minutes=90,
            force_exit_before_close_minutes=30, emergency_exit_before_close_minutes=15,
        )
        self.assertTrue(decision.market_open)
        self.assertFalse(decision.within_entry_window)
        self.assertTrue(decision.force_exit_due)
    def test_normal_session_uses_five_minute_completed_bar_slots(self) -> None:
        values = completed_bar_scan_times(
            regular_open=datetime(2026, 7, 17, 9, 30, tzinfo=ET),
            regular_close=datetime(2026, 7, 17, 16, 0, tzinfo=ET),
        )
        self.assertEqual(10, values[0].hour)
        self.assertEqual((14, 30), (values[-1].hour, values[-1].minute))
        self.assertEqual(55, len(values))

    def test_early_close_uses_supplied_official_close(self) -> None:
        values = completed_bar_scan_times(
            regular_open=datetime(2026, 7, 3, 9, 30, tzinfo=ET),
            regular_close=datetime(2026, 7, 3, 13, 0, tzinfo=ET),
        )
        self.assertEqual((11, 30), (values[-1].hour, values[-1].minute))

    def test_unknown_timezone_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            completed_bar_scan_times(
                regular_open=datetime(2026, 7, 17, 9, 30),
                regular_close=datetime(2026, 7, 17, 16, 0),
            )


if __name__ == "__main__":
    unittest.main()

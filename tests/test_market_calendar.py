from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from monitoring.market_calendar import (
    EXCHANGE_TIMEZONE,
    SESSION_TIMEZONE,
    easter_sunday,
    good_friday,
    is_early_close,
    is_market_open,
    is_market_open_today,
    market_date_now,
    market_holidays,
    next_market_open_date,
    previous_market_open_date,
)


class GoodFridayRegressionTests(unittest.TestCase):
    """The old hardcoded table had the WRONG Good Friday dates and no coverage
    past 2027. These lock in the corrected, rule-derived values."""

    def test_good_friday_known_years(self) -> None:
        # Good Friday = Easter Sunday - 2 days (Gregorian / Anonymous algorithm).
        self.assertEqual(date(2024, 3, 29), good_friday(2024))
        self.assertEqual(date(2025, 4, 18), good_friday(2025))
        self.assertEqual(date(2026, 4, 3), good_friday(2026))  # was wrongly 03-27
        self.assertEqual(date(2027, 3, 26), good_friday(2027))  # was wrongly 04-09
        self.assertEqual(date(2028, 4, 14), good_friday(2028))  # previously uncovered

    def test_easter_sunday_known_years(self) -> None:
        self.assertEqual(date(2026, 4, 5), easter_sunday(2026))
        self.assertEqual(date(2027, 3, 28), easter_sunday(2027))

    def test_market_closed_on_good_friday(self) -> None:
        self.assertFalse(is_market_open(date(2026, 4, 3)))
        self.assertFalse(is_market_open(date(2027, 3, 26)))
        self.assertFalse(is_market_open(date(2028, 4, 14)))


class HolidayDerivationTests(unittest.TestCase):
    def test_2026_holiday_set(self) -> None:
        expected = {
            date(2026, 1, 1),   # New Year's Day (Thursday)
            date(2026, 1, 19),  # MLK Jr. Day
            date(2026, 2, 16),  # Presidents' Day
            date(2026, 4, 3),   # Good Friday
            date(2026, 5, 25),  # Memorial Day
            date(2026, 6, 19),  # Juneteenth (Friday)
            date(2026, 7, 3),   # Independence Day observed (Jul 4 is Saturday)
            date(2026, 9, 7),   # Labor Day
            date(2026, 11, 26), # Thanksgiving
            date(2026, 12, 25), # Christmas (Friday)
        }
        self.assertEqual(expected, market_holidays(2026))

    def test_weekend_observance_shifts(self) -> None:
        # 2027-12-25 is a Saturday -> observed on Friday 12-24.
        self.assertIn(date(2027, 12, 24), market_holidays(2027))
        self.assertFalse(is_market_open(date(2027, 12, 24)))
        # 2028-12-25 is a Monday -> observed as-is.
        self.assertIn(date(2028, 12, 25), market_holidays(2028))

    def test_new_year_saturday_keeps_dec_31_open(self) -> None:
        # 2028-01-01 is a Saturday. NYSE does NOT close the preceding Friday
        # (Dec 31 2027) for New Year — the documented exception. Dec 31 stays open,
        # and no New Year closure lands in either year's set.
        self.assertNotIn(date(2027, 12, 31), market_holidays(2027))
        self.assertTrue(is_market_open(date(2027, 12, 31)))
        self.assertNotIn(date(2028, 1, 1), market_holidays(2028))

    def test_new_year_sunday_shifts_to_monday(self) -> None:
        # 2023-01-01 is a Sunday -> observed Monday 2023-01-02.
        self.assertIn(date(2023, 1, 2), market_holidays(2023))
        self.assertFalse(is_market_open(date(2023, 1, 2)))

    def test_weekends_closed(self) -> None:
        self.assertFalse(is_market_open(date(2026, 7, 25)))  # Saturday
        self.assertFalse(is_market_open(date(2026, 7, 26)))  # Sunday

    def test_normal_weekday_open(self) -> None:
        self.assertTrue(is_market_open(date(2026, 7, 22)))  # Wednesday


class EarlyCloseTests(unittest.TestCase):
    def test_day_after_thanksgiving_is_early_close(self) -> None:
        # Thanksgiving 2026 = Nov 26 -> day after = Nov 27.
        self.assertTrue(is_early_close(date(2026, 11, 27)))

    def test_christmas_eve_trading_day_early_close(self) -> None:
        # 2026-12-24 is a Thursday (trading day) -> early close.
        self.assertTrue(is_early_close(date(2026, 12, 24)))

    def test_early_close_only_on_trading_days(self) -> None:
        # A holiday itself is not an "early close".
        self.assertFalse(is_early_close(date(2026, 12, 25)))
        # A random full trading day is not an early close.
        self.assertFalse(is_early_close(date(2026, 7, 22)))


class TimezoneAnchoringTests(unittest.TestCase):
    def test_exchange_and_session_timezones(self) -> None:
        self.assertEqual(ZoneInfo("America/New_York"), EXCHANGE_TIMEZONE)
        self.assertEqual(ZoneInfo("America/Los_Angeles"), SESSION_TIMEZONE)

    def test_market_date_now_uses_exchange_date(self) -> None:
        # 2026-07-22 23:30 Pacific is already 2026-07-23 in New York.
        pacific = datetime(2026, 7, 22, 23, 30, tzinfo=SESSION_TIMEZONE)
        self.assertEqual(date(2026, 7, 23), market_date_now(pacific))

    def test_is_market_open_today_is_timezone_anchored(self) -> None:
        # Friday 23:30 PT == Saturday in NY -> closed.
        friday_late_pt = datetime(2026, 7, 24, 23, 30, tzinfo=SESSION_TIMEZONE)
        self.assertFalse(is_market_open_today(friday_late_pt))


class NavigationTests(unittest.TestCase):
    def test_next_skips_weekend_and_holiday(self) -> None:
        # From Thursday 2026-07-02, next open skips Fri Jul 3 (July 4 observed).
        self.assertEqual(date(2026, 7, 6), next_market_open_date(date(2026, 7, 2)))

    def test_previous_skips_weekend(self) -> None:
        # Before Monday 2026-07-20 is Friday 2026-07-17.
        self.assertEqual(date(2026, 7, 17), previous_market_open_date(date(2026, 7, 20)))


if __name__ == "__main__":
    unittest.main()

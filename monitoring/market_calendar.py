"""
Market calendar utilities.

Determines if a given date is a market-open day (US equities).
Accounts for weekends and major US holidays.
"""

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

SESSION_TIMEZONE = ZoneInfo("America/Los_Angeles")

# US market holidays (2026)
# Source: https://www.nasdaq.com/market-activity/holidays-and-hours
US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Jr. Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 3, 27),  # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed, Friday)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

# Extended through 2027 for planning
US_MARKET_HOLIDAYS_2027 = {
    date(2027, 1, 1),   # New Year's Day
    date(2027, 1, 18),  # MLK Jr. Day
    date(2027, 2, 15),  # Presidents' Day
    date(2027, 4, 9),   # Good Friday
    date(2027, 5, 31),  # Memorial Day
    date(2027, 6, 18),  # Juneteenth
    date(2027, 7, 5),   # Independence Day (observed, Monday)
    date(2027, 9, 6),   # Labor Day
    date(2027, 11, 25), # Thanksgiving
    date(2027, 12, 25), # Christmas
}

ALL_MARKET_HOLIDAYS = US_MARKET_HOLIDAYS_2026 | US_MARKET_HOLIDAYS_2027


def is_market_open_today() -> bool:
    """Check if today (in PT) is a market-open day."""
    return is_market_open(date.today())


def is_market_open(target_date: date) -> bool:
    """
    Check if a given date is a market-open day.

    Market is open Monday-Friday, excluding US holidays.
    """
    # Check if it's a weekend
    if target_date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Check if it's a known holiday
    if target_date in ALL_MARKET_HOLIDAYS:
        return False

    return True


def next_market_open_date(from_date: Optional[date] = None) -> date:
    """Find the next market-open date from a given date (default: today)."""
    if from_date is None:
        from_date = date.today()

    current = from_date + timedelta(days=1)
    while not is_market_open(current):
        current += timedelta(days=1)

    return current


def previous_market_open_date(from_date: Optional[date] = None) -> date:
    """Find the previous market-open date from a given date (default: today)."""
    if from_date is None:
        from_date = date.today()

    current = from_date - timedelta(days=1)
    while not is_market_open(current):
        current -= timedelta(days=1)

    return current


if __name__ == "__main__":
    # Quick test
    today = date.today()
    print(f"Today ({today}): {'Market OPEN' if is_market_open_today() else 'Market CLOSED'}")
    print(f"Next market open: {next_market_open_date()}")
    print(f"Previous market open: {previous_market_open_date()}")

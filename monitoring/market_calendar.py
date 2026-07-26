"""US equities market calendar, derived (not hardcoded) so it is correct for any year.

Holidays are computed from the NYSE/Nasdaq rules (fixed dates with Saturday->Friday
/ Sunday->Monday observance, plus rule-based Mondays/Thursdays and a computed Good
Friday). This replaces an earlier hardcoded table that had the wrong Good Friday
dates and no coverage past 2027. "Today" is anchored to the exchange timezone
(America/New_York), not the host's local date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SESSION_TIMEZONE = ZoneInfo("America/Los_Angeles")
EXCHANGE_TIMEZONE = ZoneInfo("America/New_York")

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) `weekday` of the month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` of the month."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """NYSE observance: Saturday holiday -> preceding Friday; Sunday -> following Monday."""
    if d.weekday() == SAT:
        return d - timedelta(days=1)
    if d.weekday() == SUN:
        return d + timedelta(days=1)
    return d


def easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def market_holidays(year: int) -> set[date]:
    """Full-day US equity market closures for the given year, computed from rules."""
    holidays = {
        _nth_weekday(year, 1, MON, 3),         # MLK Jr. Day (3rd Mon Jan)
        _nth_weekday(year, 2, MON, 3),         # Presidents' Day (3rd Mon Feb)
        good_friday(year),                     # Good Friday (computed)
        _last_weekday(year, 5, MON),           # Memorial Day (last Mon May)
        _observed(date(year, 6, 19)),          # Juneteenth
        _observed(date(year, 7, 4)),           # Independence Day
        _nth_weekday(year, 9, MON, 1),         # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, THU, 4),        # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),         # Christmas
    }
    # New Year's Day has a documented NYSE exception: a Sunday Jan 1 is observed
    # the following Monday, but a Saturday Jan 1 is NOT observed on the preceding
    # Friday (Dec 31 stays a trading day), unlike every other holiday.
    new_year = date(year, 1, 1)
    if new_year.weekday() == SUN:
        holidays.add(date(year, 1, 2))
    elif new_year.weekday() != SAT:
        holidays.add(new_year)
    return holidays


def is_market_open(target_date: date) -> bool:
    """True if `target_date` is a regular US equities trading day."""
    if target_date.weekday() >= SAT:
        return False
    if target_date in market_holidays(target_date.year):
        return False
    return True


def is_early_close(target_date: date) -> bool:
    """True on the standard NYSE half-days (1pm ET early close)."""
    if not is_market_open(target_date):
        return False
    year = target_date.year
    thanksgiving = _nth_weekday(year, 11, THU, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    if target_date == day_after_thanksgiving:
        return True
    # Christmas Eve, when it is itself a trading day.
    if target_date == date(year, 12, 24) and target_date.weekday() < SAT:
        return True
    # July 3, when it is a trading day preceding a July 4 close.
    if target_date == date(year, 7, 3) and target_date.weekday() < SAT:
        return True
    return False


def market_date_now(now: datetime | None = None) -> date:
    """The current exchange (New York) calendar date."""
    if now is None:
        now = datetime.now(EXCHANGE_TIMEZONE)
    return now.astimezone(EXCHANGE_TIMEZONE).date()


def is_market_open_today(now: datetime | None = None) -> bool:
    """True if the current exchange date is a trading day (timezone-anchored)."""
    return is_market_open(market_date_now(now))


def next_market_open_date(from_date: date | None = None) -> date:
    current = (from_date or market_date_now()) + timedelta(days=1)
    while not is_market_open(current):
        current += timedelta(days=1)
    return current


def previous_market_open_date(from_date: date | None = None) -> date:
    current = (from_date or market_date_now()) - timedelta(days=1)
    while not is_market_open(current):
        current -= timedelta(days=1)
    return current


if __name__ == "__main__":
    today = market_date_now()
    print(f"Exchange date {today}: {'OPEN' if is_market_open(today) else 'CLOSED'}"
          f"{' (early close)' if is_early_close(today) else ''}")
    print(f"Good Friday {today.year}: {good_friday(today.year)}")

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


SESSION_TIMEZONE = ZoneInfo("America/Los_Angeles")

# The single source of truth for one observation day. The launchd worker, the
# expectation pre-registration command, and the preopen qualification gate must
# all derive from this table so a date or time change cannot silently diverge.
DAILY_SLOTS: dict[tuple[int, int], tuple[str, str]] = {
    (6, 10): ("CANARY", "SPY"),
    (6, 35): ("MARKET_GATE", "SPY"),
    (7, 3): ("PILOT_SAMPLE", "SPY"),
    (7, 23): ("PILOT_SAMPLE", "QQQ"),
    (7, 43): ("PILOT_SAMPLE", "AAPL"),
    (8, 3): ("PILOT_SAMPLE", "MSFT"),
    (8, 23): ("PILOT_SAMPLE", "NVDA"),
    (8, 43): ("PILOT_SAMPLE", "AMZN"),
    (9, 3): ("PILOT_SAMPLE", "META"),
    (9, 23): ("PILOT_SAMPLE", "GOOGL"),
    (9, 43): ("PILOT_SAMPLE", "TSLA"),
    (10, 3): ("PILOT_SAMPLE", "AMD"),
    (10, 23): ("PILOT_SAMPLE", "SOFI"),
    (10, 43): ("PILOT_SAMPLE", "XOM"),
    (11, 3): ("PILOT_SAMPLE", "SPY"),
    (11, 23): ("PILOT_SAMPLE", "QQQ"),
    (13, 5): ("CLOSE_SUMMARY", "SPY"),
}


def run_id_for(kind: str, scheduled: datetime) -> str:
    stamp = scheduled.strftime("%Y%m%d-%H%M")
    if kind == "MARKET_GATE":
        return f"market-gate-{stamp}"
    if kind == "CANARY":
        return f"launchd-canary-{stamp}"
    if kind == "CLOSE_SUMMARY":
        return f"pilot-close-canary-{stamp}"
    return f"pilot-{stamp}"


def expected_runs_for_date(
    day: date, timezone: ZoneInfo = SESSION_TIMEZONE
) -> tuple[tuple[str, datetime], ...]:
    """Return every (run_id, scheduled_for) expectation for one observation day."""

    runs: list[tuple[str, datetime]] = []
    for (hour, minute), (kind, _symbol) in sorted(DAILY_SLOTS.items()):
        scheduled = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone)
        runs.append((run_id_for(kind, scheduled), scheduled))
    return tuple(runs)

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
    # Sampling used to stop here, at 11:23, while the market runs to 13:00. With
    # a 60-minute holding horizon that made every candidate opened after 10:23
    # STRUCTURALLY IMPOSSIBLE to close: AMD opened 2026-08-03 10:43 needed a
    # 11:43 observation that no slot provided, and SOFI opened 2026-08-13 10:23
    # came due at 11:23:05, five seconds after the last slot fired. Both are
    # still open with no outcome. Signals cluster late in the window (5 of the
    # 6 this period fired at or after 10:23), so the most productive hours were
    # producing positions that could never resolve. The window now reaches the
    # close.
    (11, 43): ("PILOT_SAMPLE", "AAPL"),
    (12, 3): ("PILOT_SAMPLE", "MSFT"),
    (12, 23): ("PILOT_SAMPLE", "NVDA"),
    (12, 43): ("PILOT_SAMPLE", "AMZN"),
    (13, 5): ("CLOSE_SUMMARY", "SPY"),
}

# The last slot that can observe a trajectory reaching its horizon. Opening a
# candidate whose horizon falls after this produces a position with no possible
# outcome, which is worse than not opening it: it consumes the day's one
# candidate and yields no data.
LAST_PILOT_SLOT = max(hm for hm, (kind, _s) in DAILY_SLOTS.items() if kind == "PILOT_SAMPLE")


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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class InstrumentSession:
    symbol: str
    regular_open: datetime
    regular_close: datetime
    source: str


@dataclass(frozen=True)
class SessionWindowDecision:
    market_open: bool
    within_entry_window: bool
    force_exit_due: bool
    emergency_exit_due: bool
    reasons: tuple[str, ...]


def evaluate_instrument_session(
    session: InstrumentSession | None,
    *,
    now: datetime,
    entry_delay_minutes: int,
    stop_before_close_minutes: int,
    force_exit_before_close_minutes: int,
    emergency_exit_before_close_minutes: int,
) -> SessionWindowDecision:
    """Evaluate an authoritative, product-specific official session."""

    if session is None or not session.source:
        return SessionWindowDecision(False, False, False, False, ("OFFICIAL_SESSION_UNKNOWN",))
    values = (now, session.regular_open, session.regular_close)
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        return SessionWindowDecision(False, False, False, False, ("SESSION_TIME_NOT_TIMEZONE_AWARE",))
    if session.regular_close <= session.regular_open:
        return SessionWindowDecision(False, False, False, False, ("OFFICIAL_SESSION_INVALID",))
    market_open = session.regular_open <= now < session.regular_close
    entry_start = session.regular_open + timedelta(minutes=entry_delay_minutes)
    entry_end = session.regular_close - timedelta(minutes=stop_before_close_minutes)
    force_at = session.regular_close - timedelta(minutes=force_exit_before_close_minutes)
    emergency_at = session.regular_close - timedelta(minutes=emergency_exit_before_close_minutes)
    return SessionWindowDecision(
        market_open,
        market_open and entry_start <= now <= entry_end,
        market_open and now >= force_at,
        market_open and now >= emergency_at,
        (),
    )


def completed_bar_scan_times(
    *,
    regular_open: datetime,
    regular_close: datetime,
    entry_delay_minutes: int = 30,
    stop_before_close_minutes: int = 90,
    interval_minutes: int = 5,
) -> tuple[datetime, ...]:
    """Build scan times from official session boundaries, including early closes."""

    if (
        regular_open.tzinfo is None
        or regular_open.utcoffset() is None
        or regular_close.tzinfo is None
        or regular_close.utcoffset() is None
    ):
        raise ValueError("Official market session timestamps must include timezone.")
    if regular_close <= regular_open or interval_minutes <= 0:
        raise ValueError("Invalid market session or interval.")
    first = regular_open + timedelta(minutes=entry_delay_minutes)
    last = regular_close - timedelta(minutes=stop_before_close_minutes)
    if first > last:
        return ()
    values: list[datetime] = []
    current = first
    while current <= last:
        values.append(current)
        current += timedelta(minutes=interval_minutes)
    return tuple(values)

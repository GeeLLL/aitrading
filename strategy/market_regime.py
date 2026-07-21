from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from datetime import datetime, timezone
from typing import Iterable


class MarketRegime(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class CompletedMarketBar:
    symbol: str
    close: Decimal | None
    vwap: Decimal | None
    ema_fast: Decimal | None
    ema_slow: Decimal | None
    interval_minutes: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    source_updated_at: datetime | None = None
    received_at: datetime | None = None
    completed: bool | None = None


@dataclass(frozen=True)
class MarketRegimeDecision:
    regime: MarketRegime
    reasons: tuple[str, ...]


def validate_completed_bar(
    bar: CompletedMarketBar,
    *,
    decision_time: datetime,
    expected_interval_minutes: int,
    maximum_receipt_delay_seconds: int,
) -> tuple[str, ...]:
    """Validate bar provenance and prevent future/incomplete data usage."""

    reasons: list[str] = []
    # Robinhood historical bars identify the interval but do not currently
    # promise a per-bar ``updated_at`` value.  The immutable collection receipt
    # is therefore the required provenance boundary; source_updated_at is
    # validated when the broker actually supplies it, but is not invented.
    required_values = (bar.started_at, bar.ended_at, bar.received_at)
    if any(value is None for value in required_values):
        return (f"{bar.symbol}_BAR_TIME_METADATA_MISSING",)
    assert bar.started_at and bar.ended_at and bar.received_at
    available_values = required_values + ((bar.source_updated_at,) if bar.source_updated_at else ())
    if any(value.tzinfo is None or value.utcoffset() is None for value in available_values):
        return (f"{bar.symbol}_BAR_TIME_NOT_TIMEZONE_AWARE",)
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        return ("DECISION_TIME_NOT_TIMEZONE_AWARE",)
    if bar.completed is not True:
        reasons.append(f"{bar.symbol}_BAR_NOT_COMPLETED")
    if bar.interval_minutes != expected_interval_minutes:
        reasons.append(f"{bar.symbol}_BAR_INTERVAL_INVALID")
    duration = (bar.ended_at - bar.started_at).total_seconds()
    if duration != expected_interval_minutes * 60:
        reasons.append(f"{bar.symbol}_BAR_DURATION_INVALID")
    if not (bar.started_at < bar.ended_at <= bar.received_at):
        reasons.append(f"{bar.symbol}_BAR_TIMELINE_INVALID")
    if bar.source_updated_at is not None and not (
        bar.ended_at <= bar.source_updated_at <= bar.received_at
    ):
        reasons.append(f"{bar.symbol}_BAR_SOURCE_TIMELINE_INVALID")
    decision_utc = decision_time.astimezone(timezone.utc)
    if bar.ended_at.astimezone(timezone.utc) > decision_utc:
        reasons.append(f"{bar.symbol}_BAR_FROM_FUTURE")
    return tuple(reasons)


def validate_bar_set(
    bars: Iterable[CompletedMarketBar],
    *,
    decision_time: datetime,
    expected_interval_minutes: int,
    maximum_receipt_delay_seconds: int,
    maximum_latest_bar_lag_seconds: int | None = None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    seen: set[tuple[str, datetime | None]] = set()
    last_end: dict[str, datetime] = {}
    for bar in bars:
        reasons.extend(validate_completed_bar(
            bar,
            decision_time=decision_time,
            expected_interval_minutes=expected_interval_minutes,
            maximum_receipt_delay_seconds=maximum_receipt_delay_seconds,
        ))
        key = (bar.symbol, bar.ended_at)
        if key in seen:
            reasons.append(f"{bar.symbol}_BAR_DUPLICATE")
        seen.add(key)
        if bar.ended_at is not None and bar.symbol in last_end and bar.ended_at <= last_end[bar.symbol]:
            reasons.append(f"{bar.symbol}_BAR_OUT_OF_ORDER")
        if bar.ended_at is not None:
            last_end[bar.symbol] = bar.ended_at
    # Freshness applies to the newest completed interval per symbol, not to
    # every historical bar in the lookback batch.  A completed five-minute bar
    # can legitimately be almost one interval old when queried.
    decision_utc = (
        decision_time.astimezone(timezone.utc)
        if decision_time.tzinfo is not None and decision_time.utcoffset() is not None
        else None
    )
    if decision_utc is not None:
        maximum_lag = (
            maximum_latest_bar_lag_seconds
            if maximum_latest_bar_lag_seconds is not None
            else expected_interval_minutes * 60 + maximum_receipt_delay_seconds
        )
        for symbol, ended_at in last_end.items():
            lag = (decision_utc - ended_at.astimezone(timezone.utc)).total_seconds()
            if lag > maximum_lag:
                reasons.append(f"{symbol}_LATEST_COMPLETED_BAR_STALE")
    return tuple(dict.fromkeys(reasons))


def _bar_direction(bar: CompletedMarketBar) -> MarketRegime | None:
    values = (bar.close, bar.vwap, bar.ema_fast, bar.ema_slow)
    if any(value is None for value in values):
        return None
    if bar.close > bar.vwap and bar.ema_fast > bar.ema_slow:
        return MarketRegime.BULLISH
    if bar.close < bar.vwap and bar.ema_fast < bar.ema_slow:
        return MarketRegime.BEARISH
    return MarketRegime.NO_TRADE


def determine_market_regime(
    bars: Iterable[CompletedMarketBar],
    *,
    reference_symbols: tuple[str, ...] = ("SPY", "QQQ"),
    confirmation_bars: int = 2,
) -> MarketRegimeDecision:
    """Classify market direction from completed bars; unknowns fail closed."""

    grouped: dict[str, list[CompletedMarketBar]] = {
        symbol: [] for symbol in reference_symbols
    }
    for bar in bars:
        if bar.symbol in grouped:
            grouped[bar.symbol].append(bar)

    reasons: list[str] = []
    directions: list[MarketRegime] = []
    for symbol in reference_symbols:
        symbol_bars = grouped[symbol]
        if len(symbol_bars) < confirmation_bars:
            reasons.append(f"{symbol}_CONFIRMATION_BARS_MISSING")
            continue
        selected = symbol_bars[-confirmation_bars:]
        selected_directions = [_bar_direction(bar) for bar in selected]
        if any(direction is None for direction in selected_directions):
            reasons.append(f"{symbol}_INDICATOR_UNKNOWN")
            continue
        if any(direction is MarketRegime.NO_TRADE for direction in selected_directions):
            reasons.append(f"{symbol}_INDICATORS_NOT_ALIGNED")
            continue
        if len(set(selected_directions)) != 1:
            reasons.append(f"{symbol}_DIRECTION_NOT_CONFIRMED")
            continue
        directions.append(selected_directions[0])

    if reasons:
        return MarketRegimeDecision(MarketRegime.NO_TRADE, tuple(reasons))
    if len(directions) != len(reference_symbols) or len(set(directions)) != 1:
        return MarketRegimeDecision(
            MarketRegime.NO_TRADE,
            ("REFERENCE_SYMBOLS_DISAGREE",),
        )
    return MarketRegimeDecision(directions[0], ())

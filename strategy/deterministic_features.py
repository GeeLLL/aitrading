from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from strategy.market_regime import CompletedMarketBar
from strategy.underlying_signal import UnderlyingSignalSnapshot


@dataclass(frozen=True)
class RawOhlcvBar:
    symbol: str
    started_at: datetime
    ended_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source_updated_at: datetime | None
    received_at: datetime
    completed: bool


def _validate_raw_bars(bars: Sequence[RawOhlcvBar], interval_minutes: int) -> None:
    if not bars:
        raise ValueError("RAW_BARS_EMPTY")
    symbol = bars[0].symbol
    previous_end: datetime | None = None
    for bar in bars:
        timestamps = (bar.started_at, bar.ended_at, bar.received_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("RAW_BAR_TIME_NOT_TIMEZONE_AWARE")
        if (
            bar.source_updated_at is not None
            and (
                bar.source_updated_at.tzinfo is None
                or bar.source_updated_at.utcoffset() is None
            )
        ):
            raise ValueError("RAW_BAR_SOURCE_TIME_NOT_TIMEZONE_AWARE")
        if bar.symbol != symbol:
            raise ValueError("RAW_BAR_SYMBOL_MIXED")
        if not bar.completed:
            raise ValueError("RAW_BAR_INCOMPLETE")
        if bar.ended_at <= bar.started_at:
            raise ValueError("RAW_BAR_INTERVAL_INVALID")
        if bar.ended_at > bar.received_at:
            raise ValueError("RAW_BAR_RECEIVED_BEFORE_END")
        if bar.source_updated_at is not None and not (
            bar.ended_at <= bar.source_updated_at <= bar.received_at
        ):
            raise ValueError("RAW_BAR_SOURCE_TIMELINE_INVALID")
        if Decimal(str((bar.ended_at - bar.started_at).total_seconds())) != Decimal(interval_minutes * 60):
            raise ValueError("RAW_BAR_INTERVAL_MISMATCH")
        if previous_end is not None and bar.started_at < previous_end:
            raise ValueError("RAW_BARS_OUT_OF_ORDER_OR_OVERLAPPING")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.low > bar.high:
            raise ValueError("RAW_BAR_OHLC_INVALID")
        if bar.volume < 0:
            raise ValueError("RAW_BAR_VOLUME_INVALID")
        previous_end = bar.ended_at


def ema(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    if period <= 0:
        raise ValueError("EMA_PERIOD_INVALID")
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    seed = sum(values[:period], Decimal("0")) / Decimal(period)
    result[period - 1] = seed
    alpha = Decimal("2") / Decimal(period + 1)
    current = seed
    for index in range(period, len(values)):
        current = (values[index] - current) * alpha + current
        result[index] = current
    return tuple(result)


def cumulative_vwap(bars: Sequence[RawOhlcvBar]) -> tuple[Decimal | None, ...]:
    cumulative_value = Decimal("0")
    cumulative_volume = 0
    result: list[Decimal | None] = []
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / Decimal("3")
        cumulative_value += typical * Decimal(bar.volume)
        cumulative_volume += bar.volume
        result.append(
            cumulative_value / Decimal(cumulative_volume)
            if cumulative_volume > 0 else None
        )
    return tuple(result)


def build_local_features(
    bars: Sequence[RawOhlcvBar],
    *,
    interval_minutes: int = 5,
    fast_ema_period: int = 9,
    slow_ema_period: int = 20,
    breakout_lookback: int = 6,
    volume_lookback: int = 20,
    confirmation_bars: int = 2,
) -> tuple[tuple[CompletedMarketBar, ...], UnderlyingSignalSnapshot]:
    """Create replayable strategy features from raw completed OHLCV only."""

    _validate_raw_bars(bars, interval_minutes)
    closes = tuple(bar.close for bar in bars)
    fast = ema(closes, fast_ema_period)
    slow = ema(closes, slow_ema_period)
    vwaps = cumulative_vwap(bars)
    latest = len(bars) - 1
    prior = bars[:latest]
    breakout_slice = prior[-breakout_lookback:]
    volume_slice = prior[-volume_lookback:]
    breakout_high = (
        max(bar.high for bar in breakout_slice)
        if len(breakout_slice) == breakout_lookback else None
    )
    breakdown_low = (
        min(bar.low for bar in breakout_slice)
        if len(breakout_slice) == breakout_lookback else None
    )
    average_volume = (
        sum((Decimal(bar.volume) for bar in volume_slice), Decimal("0"))
        / Decimal(volume_lookback)
        if len(volume_slice) == volume_lookback else None
    )
    signal = UnderlyingSignalSnapshot(
        symbol=bars[-1].symbol,
        close=bars[-1].close,
        vwap=vwaps[-1],
        ema_fast=fast[-1],
        ema_slow=slow[-1],
        breakout_high=breakout_high,
        breakdown_low=breakdown_low,
        current_volume=bars[-1].volume,
        average_volume=average_volume,
    )
    completed: list[CompletedMarketBar] = []
    for index in range(max(0, len(bars) - confirmation_bars), len(bars)):
        raw = bars[index]
        completed.append(
            CompletedMarketBar(
                raw.symbol, raw.close, vwaps[index], fast[index], slow[index],
                interval_minutes, raw.started_at, raw.ended_at,
                raw.source_updated_at, raw.received_at, raw.completed,
            )
        )
    return tuple(completed), signal

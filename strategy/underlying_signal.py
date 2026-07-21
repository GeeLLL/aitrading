from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from strategy.market_regime import MarketRegime


class SignalDirection(str, Enum):
    CALL = "CALL"
    PUT = "PUT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class UnderlyingSignalSnapshot:
    symbol: str
    close: Decimal | None
    vwap: Decimal | None
    ema_fast: Decimal | None
    ema_slow: Decimal | None
    breakout_high: Decimal | None
    breakdown_low: Decimal | None
    current_volume: int | None
    average_volume: Decimal | None


@dataclass(frozen=True)
class UnderlyingSignalDecision:
    direction: SignalDirection
    reasons: tuple[str, ...]
    volume_ratio: Decimal | None


def evaluate_underlying_signal(
    snapshot: UnderlyingSignalSnapshot,
    market_regime: MarketRegime,
    *,
    minimum_volume_ratio: Decimal = Decimal("1.50"),
) -> UnderlyingSignalDecision:
    """Evaluate one completed five-minute breakout bar; unknowns fail closed."""

    required = {
        "CLOSE_UNKNOWN": snapshot.close,
        "VWAP_UNKNOWN": snapshot.vwap,
        "EMA_FAST_UNKNOWN": snapshot.ema_fast,
        "EMA_SLOW_UNKNOWN": snapshot.ema_slow,
        "BREAKOUT_HIGH_UNKNOWN": snapshot.breakout_high,
        "BREAKDOWN_LOW_UNKNOWN": snapshot.breakdown_low,
        "CURRENT_VOLUME_UNKNOWN": snapshot.current_volume,
        "AVERAGE_VOLUME_UNKNOWN": snapshot.average_volume,
    }
    missing = tuple(code for code, value in required.items() if value is None)
    if missing:
        return UnderlyingSignalDecision(SignalDirection.NO_TRADE, missing, None)
    if snapshot.average_volume <= 0 or snapshot.current_volume < 0:
        return UnderlyingSignalDecision(
            SignalDirection.NO_TRADE,
            ("INVALID_VOLUME_DATA",),
            None,
        )

    volume_ratio = Decimal(snapshot.current_volume) / snapshot.average_volume
    if volume_ratio < minimum_volume_ratio:
        return UnderlyingSignalDecision(
            SignalDirection.NO_TRADE,
            ("VOLUME_CONFIRMATION_FAILED",),
            volume_ratio,
        )

    if market_regime is MarketRegime.BULLISH:
        reasons: list[str] = []
        if snapshot.close <= snapshot.vwap:
            reasons.append("PRICE_NOT_ABOVE_VWAP")
        if snapshot.ema_fast <= snapshot.ema_slow:
            reasons.append("BULLISH_EMA_ALIGNMENT_FAILED")
        if snapshot.close <= snapshot.breakout_high:
            reasons.append("SIX_BAR_BREAKOUT_FAILED")
        if reasons:
            return UnderlyingSignalDecision(SignalDirection.NO_TRADE, tuple(reasons), volume_ratio)
        return UnderlyingSignalDecision(SignalDirection.CALL, (), volume_ratio)

    if market_regime is MarketRegime.BEARISH:
        reasons = []
        if snapshot.close >= snapshot.vwap:
            reasons.append("PRICE_NOT_BELOW_VWAP")
        if snapshot.ema_fast >= snapshot.ema_slow:
            reasons.append("BEARISH_EMA_ALIGNMENT_FAILED")
        if snapshot.close >= snapshot.breakdown_low:
            reasons.append("SIX_BAR_BREAKDOWN_FAILED")
        if reasons:
            return UnderlyingSignalDecision(SignalDirection.NO_TRADE, tuple(reasons), volume_ratio)
        return UnderlyingSignalDecision(SignalDirection.PUT, (), volume_ratio)

    return UnderlyingSignalDecision(
        SignalDirection.NO_TRADE,
        ("MARKET_REGIME_NOT_DIRECTIONAL",),
        volume_ratio,
    )

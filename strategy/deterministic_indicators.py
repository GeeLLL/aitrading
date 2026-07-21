from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from strategy.deterministic_features import RawOhlcvBar, ema


@dataclass(frozen=True)
class DeterministicIndicatorSnapshot:
    rsi: Decimal | None
    atr: Decimal | None
    momentum: Decimal | None
    roc_pct: Decimal | None
    obv: int
    realized_volatility: Decimal | None
    volume_ratio: Decimal | None


def _sample_std(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(len(values) - 1)
    return variance.sqrt()


def build_indicator_snapshot(
    bars: Sequence[RawOhlcvBar], *, period: int = 14, volume_period: int = 20
) -> DeterministicIndicatorSnapshot:
    if period <= 1 or volume_period <= 0:
        raise ValueError("INDICATOR_PERIOD_INVALID")
    if not bars:
        raise ValueError("RAW_BARS_EMPTY")
    closes = [bar.close for bar in bars]
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    window = changes[-period:]
    rsi = None
    if len(window) == period:
        gains = sum((max(change, Decimal("0")) for change in window), Decimal("0")) / Decimal(period)
        losses = sum((max(-change, Decimal("0")) for change in window), Decimal("0")) / Decimal(period)
        rsi = Decimal("100") if losses == 0 else Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)
    true_ranges: list[Decimal] = []
    for index in range(1, len(bars)):
        bar, previous = bars[index], bars[index - 1]
        true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close)))
    atr = sum(true_ranges[-period:], Decimal("0")) / Decimal(period) if len(true_ranges) >= period else None
    momentum = closes[-1] - closes[-period - 1] if len(closes) > period else None
    roc = None
    if len(closes) > period and closes[-period - 1] != 0:
        roc = (closes[-1] / closes[-period - 1] - Decimal("1")) * Decimal("100")
    obv = 0
    for index in range(1, len(bars)):
        if closes[index] > closes[index - 1]:
            obv += bars[index].volume
        elif closes[index] < closes[index - 1]:
            obv -= bars[index].volume
    returns = [closes[index] / closes[index - 1] - Decimal("1") for index in range(1, len(closes)) if closes[index - 1] > 0]
    realized = _sample_std(returns[-period:])
    prior_volumes = [Decimal(bar.volume) for bar in bars[:-1][-volume_period:]]
    volume_ratio = None
    if len(prior_volumes) == volume_period:
        average = sum(prior_volumes, Decimal("0")) / Decimal(volume_period)
        volume_ratio = Decimal(bars[-1].volume) / average if average > 0 else None
    return DeterministicIndicatorSnapshot(rsi, atr, momentum, roc, obv, realized, volume_ratio)

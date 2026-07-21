from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class TradeResult:
    timestamp: datetime
    net_pnl_usd: Decimal
    strategy: str
    regime: str


@dataclass(frozen=True)
class PerformanceSummary:
    trades: int
    net_pnl_usd: Decimal
    expectancy_usd: Decimal | None
    win_rate: Decimal | None
    profit_factor: Decimal | None
    maximum_drawdown_usd: Decimal


def chronological_split(
    trades: Iterable[TradeResult],
    *,
    training_fraction: Decimal = Decimal("0.60"),
    validation_fraction: Decimal = Decimal("0.20"),
) -> tuple[tuple[TradeResult, ...], tuple[TradeResult, ...], tuple[TradeResult, ...]]:
    ordered = tuple(sorted(trades, key=lambda trade: trade.timestamp))
    if not Decimal("0") < training_fraction < Decimal("1"):
        raise ValueError("Invalid training fraction")
    if validation_fraction <= 0 or training_fraction + validation_fraction >= 1:
        raise ValueError("Invalid validation fraction")
    train_end = int(Decimal(len(ordered)) * training_fraction)
    validation_end = train_end + int(Decimal(len(ordered)) * validation_fraction)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def summarize_performance(trades: Iterable[TradeResult]) -> PerformanceSummary:
    ordered = tuple(sorted(trades, key=lambda trade: trade.timestamp))
    if not ordered:
        return PerformanceSummary(0, Decimal("0"), None, None, None, Decimal("0"))
    values = [trade.net_pnl_usd for trade in ordered]
    net = sum(values, Decimal("0"))
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    equity = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return PerformanceSummary(
        len(values), net, net / Decimal(len(values)),
        Decimal(len(wins)) / Decimal(len(values)),
        gross_profit / gross_loss if gross_loss > 0 else None,
        drawdown,
    )


def compare_incremental_lift(
    ai_results: Iterable[TradeResult],
    baseline_results: Iterable[TradeResult],
) -> Decimal | None:
    ai = summarize_performance(ai_results)
    baseline = summarize_performance(baseline_results)
    if ai.expectancy_usd is None or baseline.expectancy_usd is None:
        return None
    return ai.expectancy_usd - baseline.expectancy_usd

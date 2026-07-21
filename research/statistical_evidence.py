from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from research.evaluation import TradeResult, summarize_performance


@dataclass(frozen=True)
class EvidenceSummary:
    trades: int
    expectancy_usd: Decimal | None
    expectancy_ci_low_usd: Decimal | None
    expectancy_ci_high_usd: Decimal | None
    worst_trade_usd: Decimal | None
    loss_quantile_95_usd: Decimal | None
    maximum_drawdown_usd: Decimal


def summarize_with_bootstrap(
    trades: Iterable[TradeResult],
    *,
    samples: int = 2000,
    seed: int = 17,
) -> EvidenceSummary:
    values = tuple(trade.net_pnl_usd for trade in trades)
    base = summarize_performance(trades)
    if not values:
        return EvidenceSummary(0, None, None, None, None, None, base.maximum_drawdown_usd)
    if samples < 100:
        raise ValueError("BOOTSTRAP_SAMPLE_TOO_SMALL")
    rng = random.Random(seed)
    means = sorted(
        sum((rng.choice(values) for _ in values), Decimal("0")) / Decimal(len(values))
        for _ in range(samples)
    )
    low = means[int(samples * 0.025)]
    high = means[min(samples - 1, int(samples * 0.975))]
    ordered = sorted(values)
    tail_index = max(0, int(len(ordered) * 0.05) - 1)
    return EvidenceSummary(
        len(values),
        base.expectancy_usd,
        low,
        high,
        ordered[0],
        ordered[tail_index],
        base.maximum_drawdown_usd,
    )


def regime_summaries(trades: Iterable[TradeResult]) -> dict[str, EvidenceSummary]:
    grouped: dict[str, list[TradeResult]] = {}
    for trade in trades:
        grouped.setdefault(trade.regime, []).append(trade)
    return {
        regime: summarize_with_bootstrap(items)
        for regime, items in sorted(grouped.items())
    }

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

from research.evaluation import PerformanceSummary, TradeResult, summarize_performance


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    training_count: int
    test_count: int
    test_summary: PerformanceSummary


def expanding_walk_forward(
    trades: Iterable[TradeResult],
    *,
    minimum_training: int,
    test_size: int,
    selector: Callable[[tuple[TradeResult, ...]], str] | None = None,
) -> tuple[WalkForwardFold, ...]:
    """Evaluate chronological folds without allowing future observations into training."""
    ordered = tuple(sorted(trades, key=lambda trade: trade.timestamp))
    if minimum_training <= 0 or test_size <= 0:
        raise ValueError("WALK_FORWARD_WINDOW_INVALID")
    folds: list[WalkForwardFold] = []
    start = minimum_training
    fold = 1
    while start < len(ordered):
        training = ordered[:start]
        test = ordered[start:start + test_size]
        if not test:
            break
        chosen = selector(training) if selector else None
        eligible = tuple(item for item in test if chosen is None or item.strategy == chosen)
        folds.append(WalkForwardFold(fold, len(training), len(eligible), summarize_performance(eligible)))
        fold += 1
        start += test_size
    return tuple(folds)


def aggregate_walk_forward(folds: Iterable[WalkForwardFold]) -> dict[str, Decimal | int | None]:
    values = tuple(folds)
    tested = sum(fold.test_count for fold in values)
    net = sum((fold.test_summary.net_pnl_usd for fold in values), Decimal("0"))
    return {
        "folds": len(values),
        "test_trades": tested,
        "net_pnl_usd": net,
        "expectancy_usd": net / Decimal(tested) if tested else None,
    }

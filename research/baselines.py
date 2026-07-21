from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from research.evaluation import TradeResult, compare_incremental_lift, summarize_performance


@dataclass(frozen=True)
class BaselineComparison:
    ai_expectancy: Decimal | None
    deterministic_expectancy: Decimal | None
    random_expectancy: Decimal | None
    ai_lift_vs_deterministic: Decimal | None
    ai_lift_vs_random: Decimal | None


def compare_to_baselines(
    *, ai: Iterable[TradeResult], deterministic: Iterable[TradeResult], random_control: Iterable[TradeResult]
) -> BaselineComparison:
    ai_values, deterministic_values, random_values = tuple(ai), tuple(deterministic), tuple(random_control)
    return BaselineComparison(
        summarize_performance(ai_values).expectancy_usd,
        summarize_performance(deterministic_values).expectancy_usd,
        summarize_performance(random_values).expectancy_usd,
        compare_incremental_lift(ai_values, deterministic_values),
        compare_incremental_lift(ai_values, random_values),
    )

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research.evaluation import TradeResult, chronological_split, compare_incremental_lift, summarize_performance


class ResearchEvaluationTests(unittest.TestCase):
    def results(self, strategy: str, values: list[str]):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return tuple(TradeResult(start + timedelta(days=i), Decimal(value), strategy, "TEST") for i, value in enumerate(values))

    def test_chronological_split_has_out_of_sample_partition(self) -> None:
        train, validation, test = chronological_split(self.results("AI", ["1"] * 10))
        self.assertEqual((6, 2, 2), (len(train), len(validation), len(test)))
        self.assertLess(train[-1].timestamp, validation[0].timestamp)

    def test_drawdown_and_incremental_lift(self) -> None:
        ai = self.results("AI", ["2", "-1", "2"])
        baseline = self.results("BASE", ["1", "-1", "1"])
        self.assertEqual(Decimal("1"), summarize_performance(ai).maximum_drawdown_usd)
        self.assertGreater(compare_incremental_lift(ai, baseline), Decimal("0"))

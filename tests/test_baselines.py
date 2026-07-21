from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research.baselines import compare_to_baselines
from research.evaluation import TradeResult


class BaselineTests(unittest.TestCase):
    def rows(self, name, values):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        return tuple(TradeResult(start+timedelta(days=i), Decimal(value), name, "TEST") for i,value in enumerate(values))

    def test_ai_lift_is_measured_against_two_controls(self) -> None:
        result=compare_to_baselines(ai=self.rows("AI", ["2","2"]), deterministic=self.rows("D", ["1","1"]), random_control=self.rows("R", ["0","0"]))
        self.assertEqual(Decimal("1"), result.ai_lift_vs_deterministic)
        self.assertEqual(Decimal("2"), result.ai_lift_vs_random)


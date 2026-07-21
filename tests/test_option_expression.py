from __future__ import annotations

import unittest
from decimal import Decimal

from strategy.option_expression import OptionRiskSnapshot, assess_option_expression


class OptionExpressionTests(unittest.TestCase):
    def snapshot(self, **overrides):
        values = {
            "mark": Decimal("1.00"), "delta": Decimal("0.50"),
            "gamma": Decimal("0.03"), "theta": Decimal("-0.05"),
            "vega": Decimal("0.10"), "implied_volatility": Decimal("0.40"),
            "underlying_price": Decimal("100"),
            "expected_underlying_move": Decimal("3"),
            "bid": Decimal("0.98"), "ask": Decimal("1.02"),
        }
        values.update(overrides)
        return OptionRiskSnapshot(**values)

    def test_complete_snapshot_produces_cost_features(self) -> None:
        result = assess_option_expression(self.snapshot())
        self.assertTrue(result.eligible)
        self.assertEqual(Decimal("2"), result.breakeven_move_from_greeks)
        self.assertEqual(Decimal("5.00"), result.theta_cost_pct_per_day)

    def test_missing_greek_fails_closed(self) -> None:
        result = assess_option_expression(self.snapshot(vega=None))
        self.assertFalse(result.eligible)
        self.assertIn("VEGA_UNKNOWN", result.reasons)

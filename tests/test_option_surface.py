from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from strategy.option_surface import VolatilityPoint, assess_option_surface


class OptionSurfaceTests(unittest.TestCase):
    def test_surface_computes_skew_term_and_liquidity(self) -> None:
        points = (
            VolatilityPoint(date(2026,7,31), Decimal("100"), "CALL", Decimal("0.30"), Decimal("1"), Decimal("1.04"), 1000, 500),
            VolatilityPoint(date(2026,7,31), Decimal("100"), "PUT", Decimal("0.34"), Decimal("1"), Decimal("1.04"), 1000, 500),
            VolatilityPoint(date(2026,8,7), Decimal("100"), "CALL", Decimal("0.36"), Decimal("2"), Decimal("2.04"), 1000, 500),
        )
        result = assess_option_surface(points, underlying=Decimal("100"), realized_volatility=Decimal("0.20"), minimum_volume=100, minimum_open_interest=500, maximum_relative_spread=Decimal("0.05"))
        self.assertEqual(Decimal("0.04"), result.put_call_skew)
        self.assertTrue(result.exit_liquid)


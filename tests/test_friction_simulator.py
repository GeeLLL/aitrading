from __future__ import annotations

import unittest
from decimal import Decimal

from execution.friction_simulator import FillScenario, QuoteLevel, simulate_round_trip


class FrictionSimulatorTests(unittest.TestCase):
    def test_stress_is_no_better_than_base(self) -> None:
        results = simulate_round_trip(
            entry=QuoteLevel(Decimal("0.47"), Decimal("0.48"), 10, 10),
            exit=QuoteLevel(Decimal("0.50"), Decimal("0.52"), 10, 10),
        )
        by_scenario = {result.scenario: result for result in results}
        self.assertLessEqual(
            by_scenario[FillScenario.STRESS].gross_pnl_usd,
            by_scenario[FillScenario.BASE].gross_pnl_usd,
        )

    def test_unknown_size_is_disclosed(self) -> None:
        result = simulate_round_trip(
            entry=QuoteLevel(Decimal("1"), Decimal("1.01"), None, None),
            exit=QuoteLevel(Decimal("1"), Decimal("1.01"), None, None),
        )[1]
        self.assertIn("DISPLAYED_SIZE_UNKNOWN", result.reasons)

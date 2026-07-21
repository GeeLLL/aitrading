from __future__ import annotations

import unittest
from decimal import Decimal

from strategy.underlying_signal import SignalDirection, UnderlyingSignalDecision
from strategy.universe import load_universe_policy, rank_qualified_underlyings


class UniverseTests(unittest.TestCase):
    def test_policy_loads(self) -> None:
        policy = load_universe_policy()
        self.assertIn("SPY", policy["symbols"])
        self.assertEqual([75, 120, 200, 300], policy["budget_research_bands_usd"])

    def test_ranking_uses_volume_ratio_not_option_price(self) -> None:
        decisions = [
            ("AAPL", UnderlyingSignalDecision(SignalDirection.CALL, (), Decimal("1.6"))),
            ("NVDA", UnderlyingSignalDecision(SignalDirection.PUT, (), Decimal("2.1"))),
            ("SOFI", UnderlyingSignalDecision(SignalDirection.CALL, (), Decimal("9.0"))),
            ("MSFT", UnderlyingSignalDecision(SignalDirection.NO_TRADE, (), Decimal("4.0"))),
        ]
        ranked = rank_qualified_underlyings(
            decisions, allowed_symbols=["AAPL", "NVDA", "MSFT"], maximum_results=3
        )
        self.assertEqual(["NVDA", "AAPL"], [item.symbol for item in ranked])


if __name__ == "__main__":
    unittest.main()

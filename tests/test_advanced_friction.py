from __future__ import annotations

import unittest
from decimal import Decimal

from execution.advanced_friction import ExecutionAssumptions, estimate_execution


class AdvancedFrictionTests(unittest.TestCase):
    def assumptions(self, **overrides):
        values = dict(contracts=1, entry_bid=Decimal("1.00"), entry_ask=Decimal("1.02"),
                      exit_bid=Decimal("1.20"), exit_ask=Decimal("1.22"), entry_ask_size=4,
                      exit_bid_size=3, tick_size=Decimal("0.01"), latency_slippage_ticks=1,
                      per_contract_fees=Decimal("0.03"), regulatory_exit_fee=Decimal("0.02"))
        values.update(overrides)
        return ExecutionAssumptions(**values)

    def test_latency_fees_and_spread_reduce_pnl(self) -> None:
        result = estimate_execution(self.assumptions())
        self.assertEqual(1, result.filled_contracts)
        self.assertGreater(result.total_friction_usd, Decimal("0"))

    def test_unknown_size_never_assumes_fill(self) -> None:
        result = estimate_execution(self.assumptions(entry_ask_size=None))
        self.assertEqual(0, result.filled_contracts)
        self.assertIn("DISPLAYED_SIZE_UNKNOWN", result.reasons)


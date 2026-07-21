from __future__ import annotations

import unittest
from decimal import Decimal

from strategy.trade_management import (
    ExitAction,
    calculate_entry_limit,
    evaluate_exit,
    shadow_entry_filled,
)


class TradeManagementTests(unittest.TestCase):
    def exit(self, **overrides):
        arguments = {
            "entry_fill_price": Decimal("0.50"),
            "current_bid": Decimal("0.49"),
            "current_mark": Decimal("0.50"),
            "holding_minutes": 10,
            "underlying_vwap_still_valid": True,
            "force_exit_due": False,
            "emergency_exit_due": False,
            "position_and_quote_state_known": True,
        }
        arguments.update(overrides)
        return evaluate_exit(**arguments)

    def test_entry_limit_is_mid_plus_quarter_spread(self) -> None:
        self.assertEqual(Decimal("0.48"), calculate_entry_limit(Decimal("0.47"), Decimal("0.49")))

    def test_invalid_quote_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_entry_limit(Decimal("0.60"), Decimal("0.50"))

    def test_shadow_fill_requires_observed_ask_at_limit(self) -> None:
        self.assertTrue(shadow_entry_filled(observed_ask=Decimal("0.48"), limit_price=Decimal("0.48")))
        self.assertFalse(shadow_entry_filled(observed_ask=Decimal("0.49"), limit_price=Decimal("0.48")))

    def test_stop_loss_exits_at_bid(self) -> None:
        decision = self.exit(current_mark=Decimal("0.40"), current_bid=Decimal("0.39"))
        self.assertEqual(ExitAction.EXIT, decision.action)
        self.assertEqual("OPTION_STOP_LOSS", decision.reason)
        self.assertEqual(Decimal("0.39"), decision.simulated_exit_price)

    def test_profit_target_exits_at_bid(self) -> None:
        decision = self.exit(current_mark=Decimal("0.65"), current_bid=Decimal("0.64"))
        self.assertEqual("OPTION_PROFIT_TARGET", decision.reason)
        self.assertEqual(Decimal("0.64"), decision.simulated_exit_price)

    def test_vwap_invalidation_exits(self) -> None:
        self.assertEqual("UNDERLYING_VWAP_INVALIDATION", self.exit(underlying_vwap_still_valid=False).reason)

    def test_maximum_holding_time_exits(self) -> None:
        self.assertEqual("MAX_HOLDING_TIME", self.exit(holding_minutes=60).reason)

    def test_forced_time_exit(self) -> None:
        self.assertEqual("FORCED_TIME_EXIT", self.exit(force_exit_due=True).reason)

    def test_unknown_quote_requires_manual_intervention(self) -> None:
        decision = self.exit(position_and_quote_state_known=False)
        self.assertEqual(ExitAction.MANUAL_INTERVENTION, decision.action)

    def test_no_trigger_holds(self) -> None:
        self.assertEqual(ExitAction.HOLD, self.exit().action)


if __name__ == "__main__":
    unittest.main()

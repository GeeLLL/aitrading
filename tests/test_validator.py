from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from risk.models import (
    AccountSnapshot,
    OptionType,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionEffect,
)
from risk.startup_guard import load_safety_config
from risk.validator import evaluate_opening_order


NOW = datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc)


class OpeningOrderValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = load_safety_config("config/safety.toml")
        cls.shadow_config = copy.deepcopy(base)
        cls.shadow_config["system_mode"] = "SHADOW"

    def setUp(self) -> None:
        self.intent = OrderIntent(
            underlying="SPY",
            option_type=OptionType.CALL,
            action=OrderAction.BUY,
            position_effect=PositionEffect.OPEN,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=Decimal("0.62"),
            expiration=date(2026, 7, 24),
            bid=Decimal("0.60"),
            ask=Decimal("0.62"),
            quote_updated_at=NOW - timedelta(seconds=5),
            volume=1000,
            open_interest=1000,
        )
        self.account = AccountSnapshot(
            account_type="cash",
            equity=Decimal("300"),
            buying_power=Decimal("300"),
            option_position_count=0,
            equity_position_count=0,
            open_option_order_count=0,
            open_equity_order_count=0,
            consecutive_losses=0,
            entries_today=0,
            settled_cash=Decimal("300"),
            unsettled_cash=Decimal("0"),
            reserved_cash=Decimal("0"),
        )

    def evaluate(self, **overrides):
        arguments = {
            "intent": self.intent,
            "account": self.account,
            "config": copy.deepcopy(self.shadow_config),
            "now": NOW,
            "completed_live_trades": 0,
            "market_open": True,
            "within_entry_window": True,
            "near_forced_exit": False,
            "max_quote_age_seconds": 30,
            "max_relative_spread": Decimal("0.05"),
            "minimum_option_volume": 100,
            "minimum_open_interest": 100,
            "kill_switch_engaged": False,
        }
        arguments.update(overrides)
        return evaluate_opening_order(**arguments)

    def assert_violation(self, code: str, **overrides) -> None:
        decision = self.evaluate(**overrides)
        self.assertFalse(decision.approved)
        self.assertIn(code, decision.violations)

    def test_valid_shadow_candidate_is_approved_for_simulation(self) -> None:
        decision = self.evaluate()
        self.assertTrue(decision.approved)
        self.assertEqual((), decision.violations)
        self.assertEqual(Decimal("62.00"), decision.estimated_premium_usd)

    def test_unknown_kill_switch_state_fails_closed(self) -> None:
        self.assert_violation("KILL_SWITCH_STATUS_UNKNOWN", kill_switch_engaged=None)

    def test_engaged_kill_switch_fails_closed(self) -> None:
        decision = self.evaluate(kill_switch_engaged=True, kill_switch_reason="TEST")
        self.assertFalse(decision.approved)
        self.assertIn("KILL_SWITCH_ENGAGED:TEST", decision.violations)

    def test_unsettled_cash_cannot_fund_entry(self) -> None:
        self.assert_violation(
            "INSUFFICIENT_SETTLED_CASH",
            account=replace(self.account, settled_cash=Decimal("50"), unsettled_cash=Decimal("250")),
        )

    def test_read_only_mode_is_not_executable(self) -> None:
        config = copy.deepcopy(self.shadow_config)
        config["system_mode"] = "READ_ONLY"
        self.assert_violation("MODE_NOT_EXECUTABLE", config=config)

    def test_unknown_account_state_is_rejected(self) -> None:
        account = replace(
            self.account,
            account_type=None,
            equity=None,
            buying_power=None,
            option_position_count=None,
        )
        decision = self.evaluate(account=account)
        self.assertFalse(decision.approved)
        self.assertIn("ACCOUNT_TYPE_UNKNOWN", decision.violations)
        self.assertIn("ACCOUNT_EQUITY_UNKNOWN", decision.violations)
        self.assertIn("BUYING_POWER_UNKNOWN", decision.violations)
        self.assertIn("OPTION_POSITIONS_UNKNOWN", decision.violations)

    def test_existing_position_is_rejected(self) -> None:
        self.assert_violation(
            "EXISTING_OPTION_POSITION",
            account=replace(self.account, option_position_count=1),
        )

    def test_existing_order_is_rejected(self) -> None:
        self.assert_violation(
            "EXISTING_OPTION_ORDER",
            account=replace(self.account, open_option_order_count=1),
        )

    def test_sell_to_open_is_rejected(self) -> None:
        self.assert_violation(
            "OPENING_ACTION_MUST_BE_BUY",
            intent=replace(self.intent, action=OrderAction.SELL),
        )

    def test_market_order_is_rejected(self) -> None:
        self.assert_violation(
            "LIMIT_ORDER_REQUIRED",
            intent=replace(self.intent, order_type=OrderType.MARKET),
        )

    def test_more_than_one_contract_is_rejected(self) -> None:
        self.assert_violation(
            "QUANTITY_MUST_EQUAL_ONE",
            intent=replace(self.intent, quantity=2),
        )

    def test_first_stage_premium_above_75_is_rejected(self) -> None:
        self.assert_violation(
            "CURRENT_STAGE_PREMIUM_LIMIT_EXCEEDED",
            intent=replace(self.intent, limit_price=Decimal("0.76")),
        )

    def test_stage_does_not_upgrade_automatically_after_five_trades(self) -> None:
        self.assert_violation(
            "CURRENT_STAGE_PREMIUM_LIMIT_EXCEEDED",
            intent=replace(self.intent, limit_price=Decimal("0.80")),
            completed_live_trades=5,
        )

    def test_absolute_premium_above_120_is_rejected(self) -> None:
        self.assert_violation(
            "ABSOLUTE_PREMIUM_LIMIT_EXCEEDED",
            intent=replace(self.intent, limit_price=Decimal("1.21")),
        )

    def test_insufficient_buying_power_is_rejected(self) -> None:
        self.assert_violation(
            "INSUFFICIENT_BUYING_POWER",
            account=replace(self.account, buying_power=Decimal("50")),
        )

    def test_equity_kill_switch_is_enforced(self) -> None:
        self.assert_violation(
            "ACCOUNT_EQUITY_KILL_SWITCH",
            account=replace(self.account, equity=Decimal("224.99")),
        )

    def test_three_losses_pause_trading(self) -> None:
        self.assert_violation(
            "CONSECUTIVE_LOSS_PAUSE",
            account=replace(self.account, consecutive_losses=3),
        )

    def test_daily_entry_limit_is_enforced(self) -> None:
        self.assert_violation(
            "DAILY_ENTRY_LIMIT_REACHED",
            account=replace(self.account, entries_today=1),
        )

    def test_closed_market_is_rejected(self) -> None:
        self.assert_violation("MARKET_NOT_CONFIRMED_OPEN", market_open=False)

    def test_unknown_market_state_is_rejected(self) -> None:
        self.assert_violation("MARKET_NOT_CONFIRMED_OPEN", market_open=None)

    def test_near_forced_exit_is_rejected(self) -> None:
        self.assert_violation("TOO_CLOSE_TO_FORCED_EXIT", near_forced_exit=True)

    def test_stale_quote_is_rejected(self) -> None:
        self.assert_violation(
            "OPTION_QUOTE_STALE",
            intent=replace(
                self.intent,
                quote_updated_at=NOW - timedelta(seconds=31),
            ),
        )

    def test_missing_quote_timestamp_is_rejected(self) -> None:
        self.assert_violation(
            "OPTION_QUOTE_TIMESTAMP_MISSING",
            intent=replace(self.intent, quote_updated_at=None),
        )

    def test_wide_spread_is_rejected(self) -> None:
        self.assert_violation(
            "OPTION_SPREAD_TOO_WIDE",
            intent=replace(
                self.intent,
                bid=Decimal("0.50"),
                ask=Decimal("0.70"),
            ),
        )

    def test_missing_liquidity_policy_is_rejected(self) -> None:
        decision = self.evaluate(
            max_quote_age_seconds=None,
            max_relative_spread=None,
            minimum_option_volume=None,
            minimum_open_interest=None,
        )
        self.assertFalse(decision.approved)
        self.assertIn("MAX_QUOTE_AGE_NOT_CONFIGURED", decision.violations)
        self.assertIn("MAX_RELATIVE_SPREAD_NOT_CONFIGURED", decision.violations)
        self.assertIn("MINIMUM_OPTION_VOLUME_NOT_CONFIGURED", decision.violations)
        self.assertIn("MINIMUM_OPEN_INTEREST_NOT_CONFIGURED", decision.violations)

    def test_low_volume_and_open_interest_are_rejected(self) -> None:
        decision = self.evaluate(
            intent=replace(self.intent, volume=99, open_interest=99)
        )
        self.assertFalse(decision.approved)
        self.assertIn("OPTION_VOLUME_TOO_LOW", decision.violations)
        self.assertIn("OPEN_INTEREST_TOO_LOW", decision.violations)

    def test_dte_outside_range_is_rejected(self) -> None:
        too_short = replace(self.intent, expiration=date(2026, 7, 17))
        too_long = replace(self.intent, expiration=date(2026, 8, 14))
        self.assert_violation("DTE_BELOW_MINIMUM", intent=too_short)
        self.assert_violation("DTE_ABOVE_MAXIMUM", intent=too_long)


if __name__ == "__main__":
    unittest.main()

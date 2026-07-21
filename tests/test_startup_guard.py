from __future__ import annotations

import copy
import unittest

from risk.startup_guard import (
    UnsafeConfigurationError,
    load_safety_config,
    validate_safety_config,
)


class StartupGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe_config = load_safety_config("config/safety.toml")

    def changed(self, **updates):
        config = copy.deepcopy(self.safe_config)
        config.update(updates)
        return config

    def assert_rejected(self, **updates) -> None:
        with self.assertRaises(UnsafeConfigurationError):
            validate_safety_config(self.changed(**updates))

    def test_current_configuration_is_valid(self) -> None:
        validate_safety_config(copy.deepcopy(self.safe_config))

    def test_rejects_capital_above_300(self) -> None:
        self.assert_rejected(max_deployable_capital_usd=301)

    def test_rejects_premium_above_120(self) -> None:
        self.assert_rejected(absolute_max_premium_usd=121)

    def test_rejects_first_stage_premium_above_75(self) -> None:
        self.assert_rejected(first_stage_max_premium_usd=76)

    def test_rejects_more_than_one_contract(self) -> None:
        self.assert_rejected(max_contracts_per_position=2)

    def test_rejects_equity_threshold_below_225(self) -> None:
        self.assert_rejected(account_equity_kill_threshold_usd=224)

    def test_rejects_short_options(self) -> None:
        self.assert_rejected(allow_short_options=True)

    def test_rejects_overnight_holding(self) -> None:
        self.assert_rejected(allow_overnight=True)

    def test_rejects_entry_before_thirty_minutes_after_open(self) -> None:
        self.assert_rejected(entry_delay_after_open_minutes=29)

    def test_rejects_new_entries_inside_last_sixty_minutes(self) -> None:
        self.assert_rejected(stop_new_entries_before_close_minutes=59)

    def test_rejects_late_forced_exit(self) -> None:
        self.assert_rejected(force_exit_before_close_minutes=14)

    def test_rejects_live_before_quote_verification(self) -> None:
        self.assert_rejected(
            system_mode="LIVE",
            live_trading_enabled=True,
            order_tools_enabled=True,
            realtime_option_quote_verified=False,
        )

    def test_rejects_unapproved_stage_2(self) -> None:
        self.assert_rejected(
            approved_trade_stage=2,
            stage_2_manually_approved=False,
            stage_2_integrity_checks_passed=False,
        )


if __name__ == "__main__":
    unittest.main()

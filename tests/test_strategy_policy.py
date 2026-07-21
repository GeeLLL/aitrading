from __future__ import annotations

import copy
import unittest

from strategy.policy import (
    InvalidStrategyPolicyError,
    load_strategy_policy,
    validate_strategy_policy,
)


class StrategyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_strategy_policy()

    def changed_eligibility(self, **updates):
        policy = copy.deepcopy(self.policy)
        policy["contract_eligibility"].update(updates)
        return policy

    def assert_rejected(self, **updates) -> None:
        with self.assertRaises(InvalidStrategyPolicyError):
            validate_strategy_policy(self.changed_eligibility(**updates))

    def test_current_policy_is_valid(self) -> None:
        validate_strategy_policy(copy.deepcopy(self.policy))

    def test_rejects_wider_spread(self) -> None:
        self.assert_rejected(maximum_relative_spread=0.10)

    def test_rejects_stale_quote_policy(self) -> None:
        self.assert_rejected(maximum_quote_age_seconds=30)

    def test_rejects_lower_volume(self) -> None:
        self.assert_rejected(minimum_option_volume=100)

    def test_rejects_lower_open_interest(self) -> None:
        self.assert_rejected(minimum_open_interest=100)

    def test_rejects_higher_stage_1_premium(self) -> None:
        self.assert_rejected(stage_1_maximum_premium_usd=120)

    def test_rejects_unknown_field_fail_open(self) -> None:
        self.assert_rejected(unknown_required_field_rejects=False)

    def test_rejects_market_filter_without_two_confirmed_bars(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["market_regime"]["confirmation_completed_bars"] = 1
        with self.assertRaises(InvalidStrategyPolicyError):
            validate_strategy_policy(policy)

    def test_rejects_lower_breakout_volume_requirement(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["underlying_signal"]["minimum_volume_ratio"] = 1.0
        with self.assertRaises(InvalidStrategyPolicyError):
            validate_strategy_policy(policy)

    def test_rejects_more_entry_reprices(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["entry_execution"]["maximum_reprices"] = 2
        with self.assertRaises(InvalidStrategyPolicyError):
            validate_strategy_policy(policy)

    def test_rejects_wider_stop_loss(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["exit_management"]["stop_loss_option_pct"] = 30.0
        with self.assertRaises(InvalidStrategyPolicyError):
            validate_strategy_policy(policy)


if __name__ == "__main__":
    unittest.main()

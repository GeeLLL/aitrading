from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from risk.models import AccountSnapshot, OptionType
from risk.startup_guard import load_safety_config
from strategy.market_regime import CompletedMarketBar
from strategy.policy import load_strategy_policy
from strategy.shadow_pipeline import (
    OptionCandidate,
    PipelineStatus,
    evaluate_shadow_candidate,
)
from strategy.underlying_signal import UnderlyingSignalSnapshot


NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)


def market_bar(symbol: str, close: str, vwap: str, fast: str, slow: str):
    second = close in {"102", "202", "198"}
    ended = NOW - timedelta(minutes=5 if second else 10)
    return CompletedMarketBar(
        symbol, Decimal(close), Decimal(vwap), Decimal(fast), Decimal(slow),
        5, ended - timedelta(minutes=5), ended,
        ended + timedelta(seconds=1), ended + timedelta(seconds=2), True,
    )


class ShadowPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market_bars = [
            market_bar("SPY", "101", "100", "101", "100"),
            market_bar("SPY", "102", "100", "101", "100"),
            market_bar("QQQ", "201", "200", "201", "200"),
            market_bar("QQQ", "202", "200", "201", "200"),
        ]
        self.snapshot = UnderlyingSignalSnapshot(
            symbol="SOFI",
            close=Decimal("17.60"),
            vwap=Decimal("17.30"),
            ema_fast=Decimal("17.50"),
            ema_slow=Decimal("17.35"),
            breakout_high=Decimal("17.55"),
            breakdown_low=Decimal("17.00"),
            current_volume=1800,
            average_volume=Decimal("1000"),
        )
        self.option = OptionCandidate(
            underlying="SOFI",
            option_type=OptionType.CALL,
            strike=Decimal("17.5"),
            expiration=date(2026, 7, 24),
            bid=Decimal("0.47"),
            ask=Decimal("0.48"),
            delta=Decimal("0.47"),
            quote_updated_at=NOW - timedelta(seconds=2),
            volume=6500,
            open_interest=5300,
            earnings_date=date(2026, 7, 29),
            quote_received_at=NOW - timedelta(seconds=1),
        )
        self.account = AccountSnapshot(
            "cash", Decimal("300"), Decimal("300"), 0, 0, 0, 0, 0, 0,
            Decimal("300"), Decimal("0"), Decimal("0")
        )
        self.safety = load_safety_config("config/safety.toml")
        self.policy = load_strategy_policy()

    def evaluate(self, **overrides):
        arguments = {
            "market_bars": self.market_bars,
            "underlying_snapshot": self.snapshot,
            "option": self.option,
            "account": self.account,
            "safety_config": self.safety,
            "strategy_policy": self.policy,
            "now": NOW,
            "completed_live_trades": 0,
            "market_open": True,
            "within_entry_window": True,
            "near_forced_exit": False,
            "pilot_mode": True,
        }
        arguments.update(overrides)
        return evaluate_shadow_candidate(**arguments)

    def test_complete_chain_produces_pilot_entry_only(self) -> None:
        decision = self.evaluate()
        self.assertEqual(PipelineStatus.PILOT_SIMULATED_ENTRY, decision.status)
        self.assertIn("PILOT_NOT_STRATEGY_EVIDENCE", decision.reasons)
        self.assertEqual(Decimal("47.00"), decision.estimated_premium_usd)

    def test_design_strategy_cannot_run_without_pilot_flag(self) -> None:
        decision = self.evaluate(pilot_mode=False)
        self.assertEqual(PipelineStatus.REJECTED, decision.status)
        self.assertIn("STRATEGY_NOT_ACTIVATED_FOR_SHADOW", decision.reasons)

    def test_market_disagreement_means_no_trade(self) -> None:
        bars = list(self.market_bars)
        bars[-2:] = [
            market_bar("QQQ", "199", "200", "199", "200"),
            market_bar("QQQ", "198", "200", "199", "200"),
        ]
        self.assertEqual(PipelineStatus.NO_TRADE, self.evaluate(market_bars=bars).status)

    def test_option_direction_mismatch_is_rejected(self) -> None:
        decision = self.evaluate(option=replace(self.option, option_type=OptionType.PUT))
        self.assertIn("OPTION_DIRECTION_MISMATCH", decision.reasons)

    def test_earnings_blackout_is_rejected(self) -> None:
        decision = self.evaluate(option=replace(self.option, earnings_date=date(2026, 7, 20)))
        self.assertIn("EARNINGS_BLACKOUT", decision.reasons)

    def test_wide_spread_is_rejected_by_risk_engine(self) -> None:
        decision = self.evaluate(option=replace(self.option, bid=Decimal("0.40"), ask=Decimal("0.48")))
        self.assertIn("OPTION_SPREAD_TOO_WIDE", decision.reasons)

    def test_late_entry_is_rejected(self) -> None:
        decision = self.evaluate(within_entry_window=False, near_forced_exit=True)
        self.assertIn("OUTSIDE_ENTRY_WINDOW", decision.reasons)
        self.assertIn("TOO_CLOSE_TO_FORCED_EXIT", decision.reasons)

    def test_candidate_requires_a_fresh_final_quote_receipt(self) -> None:
        decision = self.evaluate(
            option=replace(
                self.option,
                quote_received_at=NOW - timedelta(seconds=11),
            )
        )
        self.assertIn("FINAL_OPTION_QUOTE_NOT_REFRESHED", decision.reasons)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from strategy.market_regime import MarketRegime
from strategy.underlying_signal import (
    SignalDirection,
    UnderlyingSignalSnapshot,
    evaluate_underlying_signal,
)


class UnderlyingSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = UnderlyingSignalSnapshot(
            symbol="TEST",
            close=Decimal("102"),
            vwap=Decimal("100"),
            ema_fast=Decimal("101"),
            ema_slow=Decimal("100"),
            breakout_high=Decimal("101.5"),
            breakdown_low=Decimal("98"),
            current_volume=1500,
            average_volume=Decimal("1000"),
        )

    def test_confirmed_bullish_breakout_yields_call(self) -> None:
        decision = evaluate_underlying_signal(self.snapshot, MarketRegime.BULLISH)
        self.assertEqual(SignalDirection.CALL, decision.direction)

    def test_confirmed_bearish_breakdown_yields_put(self) -> None:
        snapshot = replace(
            self.snapshot,
            close=Decimal("97"),
            vwap=Decimal("100"),
            ema_fast=Decimal("99"),
            ema_slow=Decimal("100"),
        )
        decision = evaluate_underlying_signal(snapshot, MarketRegime.BEARISH)
        self.assertEqual(SignalDirection.PUT, decision.direction)

    def test_low_volume_means_no_trade(self) -> None:
        snapshot = replace(self.snapshot, current_volume=1499)
        decision = evaluate_underlying_signal(snapshot, MarketRegime.BULLISH)
        self.assertEqual(SignalDirection.NO_TRADE, decision.direction)
        self.assertIn("VOLUME_CONFIRMATION_FAILED", decision.reasons)

    def test_missing_data_means_no_trade(self) -> None:
        snapshot = replace(self.snapshot, vwap=None)
        decision = evaluate_underlying_signal(snapshot, MarketRegime.BULLISH)
        self.assertEqual(SignalDirection.NO_TRADE, decision.direction)
        self.assertIn("VWAP_UNKNOWN", decision.reasons)

    def test_mixed_market_means_no_trade(self) -> None:
        decision = evaluate_underlying_signal(self.snapshot, MarketRegime.NO_TRADE)
        self.assertEqual(SignalDirection.NO_TRADE, decision.direction)
        self.assertIn("MARKET_REGIME_NOT_DIRECTIONAL", decision.reasons)

    def test_call_cannot_pass_below_vwap(self) -> None:
        snapshot = replace(self.snapshot, close=Decimal("99"))
        decision = evaluate_underlying_signal(snapshot, MarketRegime.BULLISH)
        self.assertEqual(SignalDirection.NO_TRADE, decision.direction)
        self.assertIn("PRICE_NOT_ABOVE_VWAP", decision.reasons)

    def test_put_cannot_pass_with_bullish_market(self) -> None:
        snapshot = replace(
            self.snapshot,
            close=Decimal("97"),
            ema_fast=Decimal("99"),
            ema_slow=Decimal("100"),
        )
        decision = evaluate_underlying_signal(snapshot, MarketRegime.BULLISH)
        self.assertEqual(SignalDirection.NO_TRADE, decision.direction)


if __name__ == "__main__":
    unittest.main()

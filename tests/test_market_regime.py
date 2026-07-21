from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from strategy.market_regime import (
    CompletedMarketBar,
    MarketRegime,
    determine_market_regime,
    validate_completed_bar,
    validate_bar_set,
)


def bar(symbol: str, close: str, vwap: str, fast: str, slow: str):
    return CompletedMarketBar(
        symbol=symbol,
        close=Decimal(close),
        vwap=Decimal(vwap),
        ema_fast=Decimal(fast),
        ema_slow=Decimal(slow),
    )


class MarketRegimeTests(unittest.TestCase):
    def test_incomplete_or_future_bar_is_rejected(self) -> None:
        now = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)
        value = CompletedMarketBar(
            "SPY", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
            5, now, now + timedelta(minutes=5), now + timedelta(minutes=5),
            now + timedelta(minutes=5), False,
        )
        reasons = validate_completed_bar(
            value, decision_time=now, expected_interval_minutes=5,
            maximum_receipt_delay_seconds=10,
        )
        self.assertIn("SPY_BAR_NOT_COMPLETED", reasons)
        self.assertIn("SPY_BAR_FROM_FUTURE", reasons)

    def test_missing_optional_source_update_is_accepted_with_receipt(self) -> None:
        now = datetime(2026, 7, 17, 15, 6, tzinfo=timezone.utc)
        value = CompletedMarketBar(
            "SPY", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
            5, now - timedelta(minutes=6), now - timedelta(minutes=1), None,
            now - timedelta(seconds=30), True,
        )
        self.assertEqual((), validate_completed_bar(
            value, decision_time=now, expected_interval_minutes=5,
            maximum_receipt_delay_seconds=10,
        ))

    def test_only_latest_bar_freshness_is_checked_for_history_batch(self) -> None:
        now = datetime(2026, 7, 17, 15, 6, tzinfo=timezone.utc)
        bars = []
        for minutes_ago in (11, 6, 1):
            ended = now - timedelta(minutes=minutes_ago)
            bars.append(CompletedMarketBar(
                "SPY", Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
                5, ended - timedelta(minutes=5), ended, None,
                now - timedelta(seconds=30), True,
            ))
        self.assertEqual((), validate_bar_set(
            bars, decision_time=now, expected_interval_minutes=5,
            maximum_receipt_delay_seconds=10,
        ))
    def test_both_references_confirm_bullish(self) -> None:
        bars = [
            bar("SPY", "101", "100", "100.5", "100"),
            bar("SPY", "102", "100.5", "101", "100.5"),
            bar("QQQ", "201", "200", "200.5", "200"),
            bar("QQQ", "202", "200.5", "201", "200.5"),
        ]
        self.assertEqual(MarketRegime.BULLISH, determine_market_regime(bars).regime)

    def test_both_references_confirm_bearish(self) -> None:
        bars = [
            bar("SPY", "99", "100", "99.5", "100"),
            bar("SPY", "98", "99.5", "99", "99.5"),
            bar("QQQ", "199", "200", "199.5", "200"),
            bar("QQQ", "198", "199.5", "199", "199.5"),
        ]
        self.assertEqual(MarketRegime.BEARISH, determine_market_regime(bars).regime)

    def test_disagreement_means_no_trade(self) -> None:
        bars = [
            bar("SPY", "101", "100", "101", "100"),
            bar("SPY", "102", "100", "101", "100"),
            bar("QQQ", "199", "200", "199", "200"),
            bar("QQQ", "198", "200", "199", "200"),
        ]
        decision = determine_market_regime(bars)
        self.assertEqual(MarketRegime.NO_TRADE, decision.regime)
        self.assertIn("REFERENCE_SYMBOLS_DISAGREE", decision.reasons)

    def test_unknown_indicator_means_no_trade(self) -> None:
        bars = [
            CompletedMarketBar("SPY", None, Decimal("100"), Decimal("101"), Decimal("100")),
            bar("SPY", "102", "100", "101", "100"),
            bar("QQQ", "201", "200", "201", "200"),
            bar("QQQ", "202", "200", "201", "200"),
        ]
        decision = determine_market_regime(bars)
        self.assertEqual(MarketRegime.NO_TRADE, decision.regime)
        self.assertIn("SPY_INDICATOR_UNKNOWN", decision.reasons)

    def test_missing_confirmation_bar_means_no_trade(self) -> None:
        bars = [
            bar("SPY", "101", "100", "101", "100"),
            bar("QQQ", "201", "200", "201", "200"),
            bar("QQQ", "202", "200", "201", "200"),
        ]
        decision = determine_market_regime(bars)
        self.assertEqual(MarketRegime.NO_TRADE, decision.regime)
        self.assertIn("SPY_CONFIRMATION_BARS_MISSING", decision.reasons)


if __name__ == "__main__":
    unittest.main()

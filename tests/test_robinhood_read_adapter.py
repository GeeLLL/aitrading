from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from execution.robinhood_read_adapter import (
    account_snapshot_from_mcp,
    completed_market_bar_from_mcp,
    option_candidate_from_mcp,
    underlying_signal_snapshot_from_mcp,
)
from risk.models import OptionType


class RobinhoodReadAdapterTests(unittest.TestCase):
    def test_option_candidate_parses_observed_mcp_shape(self) -> None:
        candidate = option_candidate_from_mcp(
            instrument={
                "chain_symbol": "SOFI",
                "type": "call",
                "strike_price": "17.5000",
                "expiration_date": "2026-07-24",
            },
            quote={
                "bid_price": "0.470000",
                "ask_price": "0.480000",
                "delta": "0.472032",
                "volume": 6585,
                "open_interest": 5380,
                "updated_at": "2026-07-17T19:12:57.702840068Z",
            },
            earnings_date="2026-07-29",
        )
        self.assertEqual("SOFI", candidate.underlying)
        self.assertEqual(OptionType.CALL, candidate.option_type)
        self.assertEqual(Decimal("0.47"), candidate.bid)
        self.assertEqual(date(2026, 7, 24), candidate.expiration)
        self.assertEqual(timezone.utc, candidate.quote_updated_at.tzinfo)

    def test_missing_quote_fields_remain_unknown(self) -> None:
        candidate = option_candidate_from_mcp(
            instrument={"chain_symbol": "SOFI", "type": "call"},
            quote={},
            earnings_date=None,
        )
        self.assertIsNone(candidate.bid)
        self.assertIsNone(candidate.ask)
        self.assertIsNone(candidate.quote_updated_at)
        self.assertIsNone(candidate.earnings_date)

    def test_unknown_option_type_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            option_candidate_from_mcp(
                instrument={"chain_symbol": "SOFI", "type": "unknown"},
                quote={},
                earnings_date=None,
            )

    def test_account_adapter_contains_only_risk_fields(self) -> None:
        snapshot = account_snapshot_from_mcp(
            account_type="cash",
            equity="300.00",
            buying_power="300.00",
            option_position_count=0,
            equity_position_count=0,
            open_option_order_count=0,
            open_equity_order_count=0,
            consecutive_losses=0,
            entries_today=0,
        )
        self.assertEqual(Decimal("300.00"), snapshot.equity)
        self.assertFalse(hasattr(snapshot, "account_number"))

    def test_market_bar_adapter(self) -> None:
        received = datetime(2026, 7, 17, 20, 1, tzinfo=timezone.utc)
        result = completed_market_bar_from_mcp(
            symbol="SPY",
            historical_bar={"begins_at": "2026-07-17T19:55:00Z", "close": "750.83"},
            indicators={"vwap": "751.43", "ema_9": "749.63", "ema_20": "749.98"},
            interval_minutes=5,
            received_at=received,
        )
        self.assertEqual(Decimal("750.83"), result.close)
        self.assertEqual(Decimal("751.43"), result.vwap)
        self.assertEqual(datetime(2026, 7, 17, 19, 55, tzinfo=timezone.utc), result.started_at)
        self.assertEqual(datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc), result.ended_at)
        self.assertIsNone(result.source_updated_at)
        self.assertTrue(result.completed)

    def test_underlying_snapshot_uses_prior_bars_only(self) -> None:
        bars = []
        for index in range(21):
            bars.append(
                {
                    "high": str(10 + index),
                    "low": str(5 + index),
                    "close": str(8 + index),
                    "volume": 100 + index,
                }
            )
        snapshot = underlying_signal_snapshot_from_mcp(
            symbol="TEST",
            completed_bars=bars,
            indicators={"vwap": "20", "ema_9": "21", "ema_20": "20"},
        )
        self.assertEqual(Decimal("29"), snapshot.breakout_high)
        self.assertEqual(Decimal("19"), snapshot.breakdown_low)
        self.assertEqual(Decimal("109.5"), snapshot.average_volume)
        self.assertEqual(120, snapshot.current_volume)

    def test_insufficient_history_fails_closed(self) -> None:
        snapshot = underlying_signal_snapshot_from_mcp(
            symbol="TEST",
            completed_bars=[{"close": "10", "high": "11", "low": "9", "volume": 100}],
            indicators={},
        )
        self.assertIsNone(snapshot.breakout_high)
        self.assertIsNone(snapshot.average_volume)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from strategy.deterministic_features import RawOhlcvBar
from strategy.deterministic_indicators import build_indicator_snapshot


class DeterministicIndicatorTests(unittest.TestCase):
    def test_same_bars_produce_same_complete_snapshot(self) -> None:
        start = datetime(2026, 7, 21, 14, 30, tzinfo=timezone.utc)
        bars = tuple(
            RawOhlcvBar("SPY", start + timedelta(minutes=5*i), start + timedelta(minutes=5*(i+1)),
                        Decimal(100+i), Decimal(102+i), Decimal(99+i), Decimal(101+i),
                        1000+i*100, None, start + timedelta(minutes=5*(i+1), seconds=1), True)
            for i in range(22)
        )
        first = build_indicator_snapshot(bars)
        self.assertEqual(first, build_indicator_snapshot(bars))
        self.assertIsNotNone(first.rsi)
        self.assertIsNotNone(first.atr)
        self.assertIsNotNone(first.volume_ratio)


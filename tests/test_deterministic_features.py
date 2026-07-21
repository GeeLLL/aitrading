from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from strategy.deterministic_features import RawOhlcvBar, build_local_features


def raw_bars(count: int = 25):
    start = datetime(2026, 7, 17, 13, 30, tzinfo=timezone.utc)
    result = []
    for index in range(count):
        opened = Decimal("100") + Decimal(index) / Decimal("10")
        began = start + timedelta(minutes=5 * index)
        ended = began + timedelta(minutes=5)
        result.append(
            RawOhlcvBar(
                "SPY", began, ended, opened, opened + Decimal("0.20"),
                opened - Decimal("0.20"), opened + Decimal("0.10"),
                1000 + index, ended, ended + timedelta(seconds=1), True,
            )
        )
    return result


class DeterministicFeatureTests(unittest.TestCase):
    def test_missing_optional_source_update_uses_receipt_provenance(self) -> None:
        bars = raw_bars()
        bars[-1] = replace(bars[-1], source_updated_at=None)
        completed, signal = build_local_features(bars)
        self.assertTrue(completed[-1].completed)
        self.assertIsNone(completed[-1].source_updated_at)
        self.assertIsNotNone(signal.close)

    def test_identical_raw_bars_produce_identical_features(self) -> None:
        first = build_local_features(raw_bars())
        second = build_local_features(raw_bars())
        self.assertEqual(first, second)
        self.assertIsNotNone(first[1].ema_fast)
        self.assertIsNotNone(first[1].ema_slow)
        self.assertEqual(2, len(first[0]))

    def test_breakout_and_average_exclude_current_bar(self) -> None:
        bars = raw_bars()
        _, signal = build_local_features(bars)
        self.assertEqual(max(bar.high for bar in bars[-7:-1]), signal.breakout_high)
        expected = sum((Decimal(bar.volume) for bar in bars[-21:-1]), Decimal("0")) / Decimal(20)
        self.assertEqual(expected, signal.average_volume)

    def test_incomplete_bar_fails_closed(self) -> None:
        bars = raw_bars()
        bars[-1] = RawOhlcvBar(**{**bars[-1].__dict__, "completed": False})
        with self.assertRaisesRegex(ValueError, "RAW_BAR_INCOMPLETE"):
            build_local_features(bars)


if __name__ == "__main__":
    unittest.main()

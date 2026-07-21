import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from research.evaluation import TradeResult
from research.statistical_evidence import regime_summaries, summarize_with_bootstrap


class StatisticalEvidenceTests(unittest.TestCase):
    def trades(self):
        start = datetime(2026, 1, 1)
        values = ["2", "-1", "3", "-2", "4"]
        return tuple(
            TradeResult(start + timedelta(days=i), Decimal(value), "ai", "trend" if i < 3 else "range")
            for i, value in enumerate(values)
        )

    def test_bootstrap_is_reproducible_and_reports_tail(self):
        a = summarize_with_bootstrap(self.trades(), samples=200, seed=7)
        b = summarize_with_bootstrap(self.trades(), samples=200, seed=7)
        self.assertEqual(a, b)
        self.assertEqual(a.trades, 5)
        self.assertEqual(a.worst_trade_usd, Decimal("-2"))
        self.assertLessEqual(a.expectancy_ci_low_usd, a.expectancy_ci_high_usd)

    def test_regime_breakdown(self):
        result = regime_summaries(self.trades())
        self.assertEqual(set(result), {"range", "trend"})

    def test_rejects_tiny_bootstrap(self):
        with self.assertRaisesRegex(ValueError, "BOOTSTRAP_SAMPLE_TOO_SMALL"):
            summarize_with_bootstrap(self.trades(), samples=10)

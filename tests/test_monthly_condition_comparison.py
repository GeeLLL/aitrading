import unittest
from datetime import datetime, timezone
from decimal import Decimal

from research.historical_experiment import Candidate
from research.monthly_condition_comparison import select_daily, summarize_condition


class MonthlyConditionComparisonTests(unittest.TestCase):
    def candidate(self, hour, symbol, ratio, pnl):
        now = datetime(2026, 7, 1, hour, tzinfo=timezone.utc)
        return Candidate(now, symbol, "CALL", Decimal("1"), Decimal("1"), Decimal(pnl), Decimal(pnl), Decimal(ratio))

    def test_two_entry_group_requires_distinct_symbols(self):
        rows = (
            self.candidate(15, "AAPL", "3", "0.01"),
            self.candidate(16, "AAPL", "4", "0.02"),
            self.candidate(17, "MSFT", "3", "-0.01"),
        )
        selected = select_daily(rows, threshold=Decimal("3"), maximum_daily_entries=2)
        self.assertEqual([item.symbol for item in selected], ["AAPL", "MSFT"])

    def test_summary_uses_active_days_for_no_trade_rate(self):
        rows = (self.candidate(15, "AAPL", "3", "0.01"),)
        result = summarize_condition(rows, threshold=Decimal("3"), maximum_daily_entries=1, sessions=5)
        self.assertEqual(result.no_trade_rate, 0.8)

import unittest
from decimal import Decimal

from research.batch_replay import ReplayTrade, summarize_batch_replay


class BatchReplayTests(unittest.TestCase):
    def test_ineligible_trades_excluded(self):
        report = summarize_batch_replay([
            ReplayTrade("d1", "ai", True, Decimal("10"), Decimal("2")),
            ReplayTrade("d1", "ai", False, Decimal("100"), Decimal("1")),
            ReplayTrade("d2", "ai", True, Decimal("-3"), Decimal("1")),
        ])
        self.assertEqual(report.sessions, 2)
        self.assertEqual(report.eligible_trades, 2)
        self.assertEqual(report.net_pnl_usd, Decimal("4"))
        self.assertEqual(report.friction_share_of_gross_profit, Decimal("0.3"))

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from research.evaluation import TradeResult
from research.walk_forward import aggregate_walk_forward, expanding_walk_forward


class WalkForwardTests(unittest.TestCase):
    def test_expanding_folds_are_chronological(self):
        start = datetime(2026, 1, 1)
        trades = [TradeResult(start + timedelta(days=i), Decimal(i - 2), "ai", "trend") for i in range(8)]
        folds = expanding_walk_forward(trades, minimum_training=4, test_size=2)
        self.assertEqual([fold.training_count for fold in folds], [4, 6])
        self.assertEqual([fold.test_count for fold in folds], [2, 2])
        total = aggregate_walk_forward(folds)
        self.assertEqual(total["folds"], 2)
        self.assertEqual(total["test_trades"], 4)

    def test_invalid_windows_rejected(self):
        with self.assertRaisesRegex(ValueError, "WALK_FORWARD_WINDOW_INVALID"):
            expanding_walk_forward([], minimum_training=0, test_size=2)

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from research.historical_experiment import Candidate, load_symbol, select_one_per_day, summarize


class HistoricalExperimentTests(unittest.TestCase):
    def test_load_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SPY.json"
            path.write_text(json.dumps({"schema_version": 1, "symbol": "SPY", "bars": [{
                "begins_at": "2026-07-17T14:00:00Z", "open": "1", "high": "2",
                "low": "0.5", "close": "1.5", "volume": 10}]}))
            bars = load_symbol(path)
            self.assertEqual(bars[0].symbol, "SPY")
            self.assertEqual(bars[0].close, Decimal("1.5"))

    def test_selects_first_signal_per_day_without_lookahead(self):
        stamp = datetime(2026, 7, 17, 15, tzinfo=timezone.utc)
        low = Candidate(stamp, "A", "CALL", Decimal("1"), Decimal("1.1"),
                        Decimal(".1"), Decimal(".099"), Decimal("1.5"))
        high = Candidate(stamp, "B", "CALL", Decimal("1"), Decimal("1.2"),
                         Decimal(".2"), Decimal(".199"), Decimal("2.0"))
        self.assertEqual(select_one_per_day((low, high)), (low,))

    def test_summary_includes_drawdown(self):
        stamp = datetime(2026, 7, 17, 15, tzinfo=timezone.utc)
        rows = (
            Candidate(stamp, "A", "CALL", Decimal("1"), Decimal("1"), Decimal(".1"), Decimal(".1"), Decimal("2")),
            Candidate(stamp, "B", "PUT", Decimal("1"), Decimal("1"), Decimal("-.2"), Decimal("-.2"), Decimal("2")),
        )
        result = summarize(2, rows)
        self.assertAlmostEqual(result.maximum_drawdown, .2)
        self.assertEqual(result.direction_accuracy, .5)


if __name__ == "__main__":
    unittest.main()

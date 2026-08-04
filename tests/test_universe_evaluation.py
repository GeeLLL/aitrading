from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from execution.raw_data_vault import RawDataVault
from research.universe_evaluation import evaluate_snapshot
from research.universe_features import (
    Bar,
    derive_features,
    exponential_moving_average,
    parse_bars,
    rolling_indicator_bars,
    session_vwap,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = {"breakout_lookback_completed_bars": 6, "volume_average_lookback_bars": 20,
          "bar_interval_minutes": 5, "minimum_volume_ratio": 1.50}


def bar(symbol: str, begins: datetime, *, close: str, high: str, low: str,
        volume: int, open_: str | None = None) -> Bar:
    return Bar(symbol, begins, Decimal(open_ or close), Decimal(high), Decimal(low),
               Decimal(close), volume, "reg")


def series(symbol: str, start: datetime, count: int, *, volume: int = 1000,
           close: str = "100") -> list[Bar]:
    return [
        bar(symbol, start + timedelta(minutes=5 * i),
            close=close, high=close, low=close, volume=volume)
        for i in range(count)
    ]


class FeatureDerivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)

    def test_average_volume_excludes_the_newest_bar(self):
        # Otherwise a volume spike partially cancels itself in its own average.
        bars = series("SPY", self.start, 21, volume=1000)
        bars[-1] = bar("SPY", bars[-1].begins_at, close="100", high="100",
                       low="100", volume=5000)
        features = derive_features(bars, POLICY)
        self.assertEqual(features["average_volume"], Decimal(1000))
        self.assertEqual(features["current_volume"], 5000)
        self.assertEqual(features["volume_ratio"], Decimal(5))

    def test_breakout_window_excludes_the_newest_bar(self):
        bars = series("SPY", self.start, 21)
        bars[-2] = bar("SPY", bars[-2].begins_at, close="100", high="110", low="90", volume=1000)
        bars[-1] = bar("SPY", bars[-1].begins_at, close="105", high="999", low="1", volume=1000)
        features = derive_features(bars, POLICY)
        self.assertEqual(features["breakout_high"], Decimal("110"))
        self.assertEqual(features["breakdown_low"], Decimal("90"))

    def test_insufficient_history_is_reported_not_estimated(self):
        features = derive_features(series("SPY", self.start, 5), POLICY)
        self.assertIsNone(features["average_volume"])
        self.assertIsNone(features["volume_ratio"])
        self.assertIn("INSUFFICIENT_BREAKOUT_LOOKBACK", features["insufficient"])
        self.assertIn("INSUFFICIENT_VOLUME_LOOKBACK", features["insufficient"])

    def test_no_bars_fails_closed(self):
        features = derive_features([], POLICY)
        self.assertIn("NO_BARS", features["insufficient"])
        self.assertIsNone(features["close"])

    def test_ema_needs_full_period(self):
        closes = [Decimal(str(x)) for x in range(1, 10)]
        self.assertIsNone(exponential_moving_average(closes, 20))
        self.assertIsNotNone(exponential_moving_average(closes, 9))

    def test_session_vwap_uses_only_the_newest_bars_own_session(self):
        yesterday = series("SPY", self.start - timedelta(days=1), 3, close="200")
        today = series("SPY", self.start, 3, close="100")
        self.assertEqual(session_vwap(yesterday + today), Decimal("100"))

    def test_extended_hours_bars_are_excluded(self):
        envelope = {"response": {"tool_results": [{
            "tool": "get_equity_historicals",
            "output": {"data": {"results": [{"symbol": "SPY", "bars": [
                {"begins_at": self.start.isoformat(), "close_price": "1", "high_price": "1",
                 "low_price": "1", "open_price": "1", "volume": 1, "session": "reg"},
                {"begins_at": (self.start + timedelta(minutes=5)).isoformat(),
                 "close_price": "2", "high_price": "2", "low_price": "2",
                 "open_price": "2", "volume": 2, "session": "pre"},
            ]}]}},
        }]}}
        grouped = parse_bars(envelope)
        self.assertEqual(len(grouped["SPY"]), 1)


class RollingIndicatorTests(unittest.TestCase):
    def test_each_bar_carries_its_own_indicator_values(self):
        start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
        bars = [
            bar("SPY", start + timedelta(minutes=5 * i), close=str(100 + i),
                high=str(100 + i), low=str(100 + i), volume=1000)
            for i in range(25)
        ]
        received = bars[-1].begins_at + timedelta(minutes=6)
        built = rolling_indicator_bars(bars, received_at=received,
                                       interval_minutes=5, count=2)
        self.assertEqual(len(built), 2)
        # A rising series means the later bar's EMA must be higher.
        self.assertLess(built[0].ema_fast, built[1].ema_fast)
        self.assertTrue(all(b.completed for b in built))
        self.assertEqual(built[0].interval_minutes, 5)
        self.assertEqual(built[-1].ended_at, bars[-1].begins_at + timedelta(minutes=5))


class SnapshotEvaluationTests(unittest.TestCase):
    def _store(self, root: Path, symbol_bars: dict[str, list[Bar]],
               received: datetime) -> Path:
        results = [{
            "tool": "get_equity_historicals",
            "output": {"data": {"results": [
                {"symbol": symbol, "bars": [
                    {"begins_at": b.begins_at.isoformat(), "close_price": str(b.close),
                     "high_price": str(b.high), "low_price": str(b.low),
                     "open_price": str(b.open), "volume": b.volume, "session": "reg"}
                    for b in bars
                ]}
                for symbol, bars in symbol_bars.items()
            ]}},
        }]
        receipt = RawDataVault(root).store(
            source="ROBINHOOD_OFFICIAL_MCP",
            request={"schema_version": 1, "symbol": "SPY", "tool_calls": []},
            response={"tool_results": results},
            source_updated_at=received - timedelta(seconds=1),
            received_at=received,
        )
        return receipt.path

    def test_stale_bars_make_every_signal_inadmissible(self):
        # The live 2026-07-30 case: a snapshot carrying the prior session's bars
        # produced "qualified" signals at volume_ratio 4.4-4.7 purely from the
        # closing volume spike. Those must never be admissible.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 29, 13, 30, tzinfo=timezone.utc)
            received = datetime(2026, 7, 30, 13, 38, tzinfo=timezone.utc)
            bars = {s: series(s, start, 25) for s in ("SPY", "QQQ")}
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            self.assertEqual(report["status"], "OK")
            self.assertFalse(report["bar_time_sound"])
            self.assertFalse(report["decision_admissible"])
            self.assertEqual(report["inadmissible_reason"], "BAR_TIME_INTEGRITY_VIOLATED")
            self.assertEqual(report["qualified_symbols"], [])

    def test_fresh_bars_are_admissible(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
            bars = {s: series(s, start, 25) for s in ("SPY", "QQQ")}
            newest_end = start + timedelta(minutes=5 * 25)
            received = newest_end + timedelta(seconds=60)
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            self.assertTrue(report["bar_time_sound"], report["bar_time_violations"])
            self.assertTrue(report["decision_admissible"])

    def test_provenance_states_no_model_was_involved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
            path = self._store(root, {"SPY": series("SPY", start, 25)},
                               start + timedelta(minutes=126))
            report = evaluate_snapshot(path, project_root=ROOT)
            self.assertEqual(
                report["provenance"], "HARVESTED_VAULT_SNAPSHOT_FROZEN_STRATEGY_CODE",
            )
            self.assertIn("No model produced any number here", report["note"])

    def test_prior_session_history_fills_the_lookback_at_the_open(self):
        """The point of the two-session probe window (BARS_PROBE_LOOKBACK_DAYS).

        A 20-bar volume average and a 20-period EMA need ~100 minutes of a
        single session, so on 2026-08-04 the first four slots each day
        (07:03-08:03 PDT) reported SPY_INDICATOR_UNKNOWN and could not produce a
        signal — 23% of the schedule, structurally dead. Reaching back one
        session fills the lookback 30 minutes after the open.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prior = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
            today = datetime(2026, 8, 4, 13, 30, tzinfo=timezone.utc)
            received = today + timedelta(minutes=33)          # ~07:03 PDT
            bars = {
                symbol: series(symbol, prior, 78) + series(symbol, today, 6)
                for symbol in ("SPY", "QQQ", "NVDA")
            }
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            self.assertTrue(report["decision_admissible"], report["bar_time_violations"])
            for symbol, row in report["symbols"].items():
                self.assertEqual(row["features"]["insufficient"], [], symbol)
                self.assertIsNotNone(row["features"]["average_volume"], symbol)
                self.assertIsNotNone(row["features"]["ema_slow"], symbol)

    def test_a_single_session_at_the_open_still_fails_closed(self):
        """The counterpart: without the prior session the same moment reports
        what is missing rather than estimating it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = datetime(2026, 8, 4, 13, 30, tzinfo=timezone.utc)
            received = today + timedelta(minutes=33)
            bars = {s: series(s, today, 6) for s in ("SPY", "QQQ")}
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            for row in report["symbols"].values():
                self.assertIn("INSUFFICIENT_VOLUME_LOOKBACK", row["features"]["insufficient"])
                self.assertIsNone(row["features"]["volume_ratio"])

    def test_venue_placeholder_grid_does_not_poison_bar_time(self):
        """LIVE 2026-08-03: get_equity_historicals returns the WHOLE regular
        session as a grid, so a 16:23Z probe came back carrying zero-volume rows
        stamped out to the 20:00Z close. The frozen validator read those as
        SPY_BAR_FROM_FUTURE and made every slot inadmissible. Rows for minutes
        that have not happened are not data and must be dropped."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
            received = start + timedelta(minutes=5 * 25)      # 25 real bars in
            bars = {}
            for symbol in ("SPY", "QQQ"):
                real = series(symbol, start, 25)
                placeholders = [
                    bar(symbol, start + timedelta(minutes=5 * i), close="100",
                        high="100", low="100", volume=0)
                    for i in range(25, 78)                    # out to the close
                ]
                bars[symbol] = real + placeholders
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            self.assertEqual(report["bar_time_violations"], [])
            self.assertTrue(report["decision_admissible"])

    def test_the_still_forming_bar_is_excluded(self):
        """The boundary is the bar's END: at a 16:23 receipt the 16:20 bar is
        still consolidating, so it must not reach the frozen evaluators."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
            bars = {s: series(s, start, 25) for s in ("SPY", "QQQ")}
            # Receipt lands three minutes into the 25th bar's five-minute window.
            received = start + timedelta(minutes=5 * 24, seconds=180)
            path = self._store(root, bars, received)

            report = evaluate_snapshot(path, project_root=ROOT)

            self.assertEqual(report["bar_time_violations"], [])
            newest = max(b["features"]["newest_bar_begins_at"]
                         for b in report["symbols"].values()
                         if b["features"].get("newest_bar_begins_at"))
            self.assertLessEqual(
                datetime.fromisoformat(newest) + timedelta(minutes=5), received,
            )

    def test_chunked_snapshots_evaluate_as_one_universe(self):
        """The universe arrives as several probes (BARS_PROBE_CHUNK_SYMBOLS), so
        the evaluator must see the union, not just the first chunk."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
            received = start + timedelta(minutes=5 * 25, seconds=60)
            first = self._store(root, {s: series(s, start, 25) for s in ("SPY", "QQQ")},
                                received)
            second = self._store(root, {"NVDA": series("NVDA", start, 25)}, received)

            report = evaluate_snapshot([first, second], project_root=ROOT)

            self.assertEqual(report["status"], "OK")
            self.assertEqual(sorted(report["symbols"]), ["NVDA", "QQQ", "SPY"])
            self.assertEqual(len(report["snapshot_paths"]), 2)

    def test_freshness_is_judged_by_the_slowest_chunk(self):
        """Chunking must never make bar-time integrity look better than it was:
        a stale chunk has to poison the whole set, not be averaged away."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
            fresh_at = start + timedelta(minutes=5 * 25, seconds=60)
            fresh = self._store(root, {s: series(s, start, 25) for s in ("SPY", "QQQ")},
                                fresh_at)
            self.assertTrue(evaluate_snapshot(fresh, project_root=ROOT)["bar_time_sound"])

            stale = self._store(root, {"NVDA": series("NVDA", start, 25)},
                                fresh_at - timedelta(hours=3))
            report = evaluate_snapshot([fresh, stale], project_root=ROOT)

            self.assertEqual(report["received_at"],
                             min(report["receipt_times"]))
            self.assertFalse(report["decision_admissible"])
            self.assertEqual(report["qualified_symbols"], [])

    def test_one_unreadable_chunk_fails_the_whole_set_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = datetime(2026, 7, 30, 13, 30, tzinfo=timezone.utc)
            good = self._store(root, {"SPY": series("SPY", start, 25)},
                               start + timedelta(minutes=126))
            bad = root / "bad.json"
            bad.write_text(json.dumps({"received_at": "nonsense"}), encoding="utf-8")
            report = evaluate_snapshot([good, bad], project_root=ROOT)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["reason"], "NO_TRUSTED_RECEIPT_TIME")

    def test_unreadable_receipt_time_fails_closed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text(json.dumps({"received_at": "nonsense"}), encoding="utf-8")
            report = evaluate_snapshot(path, project_root=ROOT)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["reason"], "NO_TRUSTED_RECEIPT_TIME")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from execution.raw_data_vault import RawDataVault
from monitoring.bar_time_checks import (
    verify_snapshot_bar_times,
    verify_symbol_bars,
    within_regular_session,
)

PT = ZoneInfo("America/Los_Angeles")
# 2026-07-28 is a Tuesday trading day.
MID_SESSION = datetime(2026, 7, 28, 10, 0, tzinfo=PT).astimezone(timezone.utc)


def bars(start: datetime, count: int, *, step_seconds: int = 300) -> list[dict]:
    return [
        {"begins_at": (start + timedelta(seconds=step_seconds * i)).isoformat().replace("+00:00", "Z")}
        for i in range(count)
    ]


class SymbolBarVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        # Newest bar completes 60s before receipt: comfortably fresh.
        self.start = MID_SESSION - timedelta(seconds=300 * 6 + 60)

    def test_clean_series_is_sound(self):
        report = verify_symbol_bars("SPY", bars(self.start, 6), received_at=MID_SESSION)
        self.assertTrue(report.sound, report.irregularities)
        self.assertEqual(report.bar_count, 6)

    def test_duplicate_bars_are_flagged(self):
        series = bars(self.start, 6)
        series.append(dict(series[-1]))
        report = verify_symbol_bars("SPY", series, received_at=MID_SESSION)
        self.assertIn("DUPLICATE_BARS", report.irregularities)

    def test_out_of_order_bars_are_flagged(self):
        series = bars(self.start, 6)
        series[1], series[3] = series[3], series[1]
        report = verify_symbol_bars("SPY", series, received_at=MID_SESSION)
        self.assertIn("BARS_OUT_OF_ORDER", report.irregularities)

    def test_missing_bar_breaks_interval_uniformity(self):
        series = bars(self.start, 6)
        del series[2]
        report = verify_symbol_bars("SPY", series, received_at=MID_SESSION)
        self.assertIn("NON_UNIFORM_BAR_INTERVAL", report.irregularities)

    def test_future_bar_is_flagged(self):
        series = bars(self.start, 6)
        series.append({"begins_at": (MID_SESSION + timedelta(seconds=300)).isoformat()})
        report = verify_symbol_bars("SPY", series, received_at=MID_SESSION)
        self.assertIn("BAR_FROM_FUTURE", report.irregularities)

    def test_stale_newest_bar_is_flagged_within_the_session(self):
        stale_start = MID_SESSION - timedelta(seconds=300 * 6 + 900)
        report = verify_symbol_bars("SPY", bars(stale_start, 6), received_at=MID_SESSION)
        self.assertIn("NEWEST_COMPLETED_BAR_STALE", report.irregularities)

    def test_prior_session_bars_are_named_distinctly(self):
        # Yesterday's bars are a different fact from "arrived a bit late".
        yesterday = MID_SESSION - timedelta(days=1)
        report = verify_symbol_bars("SPY", bars(yesterday, 6), received_at=MID_SESSION)
        self.assertIn("BARS_FROM_PRIOR_SESSION", report.irregularities)
        self.assertNotIn("NEWEST_COMPLETED_BAR_STALE", report.irregularities)

    def test_freshness_is_not_enforced_outside_the_session(self):
        yesterday = MID_SESSION - timedelta(days=1)
        report = verify_symbol_bars(
            "SPY", bars(yesterday, 6), received_at=MID_SESSION, enforce_freshness=False,
        )
        self.assertTrue(report.sound, report.irregularities)

    def test_unparsable_timestamp_fails_closed(self):
        report = verify_symbol_bars("SPY", [{"begins_at": "nonsense"}], received_at=MID_SESSION)
        self.assertFalse(report.sound)

    def test_empty_series_fails_closed(self):
        report = verify_symbol_bars("SPY", [], received_at=MID_SESSION)
        self.assertIn("NO_BARS", report.irregularities)


class SessionWindowTests(unittest.TestCase):
    def test_regular_session_window(self):
        self.assertTrue(within_regular_session(datetime(2026, 7, 28, 10, 0, tzinfo=PT)))
        self.assertFalse(within_regular_session(datetime(2026, 7, 28, 6, 10, tzinfo=PT)))
        self.assertFalse(within_regular_session(datetime(2026, 7, 28, 14, 0, tzinfo=PT)))
        # Saturday
        self.assertFalse(within_regular_session(datetime(2026, 8, 1, 10, 0, tzinfo=PT)))


class SnapshotVerificationTests(unittest.TestCase):
    def _store(self, root: Path, series: list[dict], received: datetime) -> Path:
        receipt = RawDataVault(root).store(
            source="ROBINHOOD_OFFICIAL_MCP",
            request={"schema_version": 1, "symbol": "SPY", "tool_calls": []},
            response={"tool_results": [
                {"tool": "get_equity_historicals",
                 "output": {"data": {"results": [{"symbol": "SPY", "bars": series}]}}},
            ]},
            source_updated_at=received - timedelta(seconds=1),
            received_at=received,
        )
        return receipt.path

    def test_sound_snapshot_passes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = MID_SESSION - timedelta(seconds=300 * 6 + 60)
            path = self._store(root, bars(start, 6), MID_SESSION)
            report = verify_snapshot_bar_times(path)
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["freshness_enforced"])
            self.assertEqual(report["provenance"], "HARVESTED_VAULT_SNAPSHOT")

    def test_tampered_snapshot_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = MID_SESSION - timedelta(seconds=300 * 6 + 60)
            path = self._store(root, bars(start, 6), MID_SESSION)
            path.write_bytes(path.read_bytes().replace(b"SPY", b"QQQ", 1))
            report = verify_snapshot_bar_times(path)
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("SNAPSHOT_VERIFY_FAILED", str(report["reason"]))

    def test_snapshot_without_bars_is_reported_not_crashed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = RawDataVault(root).store(
                source="ROBINHOOD_OFFICIAL_MCP",
                request={"schema_version": 1, "probe": "FRESH_OPTION_QUOTE", "tool_calls": []},
                response={"tool_results": [{"tool": "get_option_quotes", "output": {}}]},
                source_updated_at=MID_SESSION - timedelta(seconds=1),
                received_at=MID_SESSION,
            )
            report = verify_snapshot_bar_times(receipt.path)
            self.assertEqual(report["reason"], "NO_HISTORICAL_BARS_IN_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()

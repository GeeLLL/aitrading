from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.deterministic_slots import run_close_summary, run_pilot_sample

PT = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 7, 31, 10, 3, tzinfo=PT)
SCHEDULED = NOW


class _Receipt:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.snapshot_id = "snap"
        self.content_sha256 = "deadbeef"


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _bars_receipt(root: Path) -> _Receipt:
    return _Receipt(_write_json(root / "vault/bars.json", {
        "received_at": "2026-07-31T17:03:02+00:00",
        "source_updated_at": "2026-07-31T17:02:58+00:00",
        "response": {"tool_results": []},
    }))


def _option_receipt(root: Path) -> _Receipt:
    return _Receipt(_write_json(root / "vault/options.json", {
        "received_at": "2026-07-31T17:03:20+00:00",
        "source_updated_at": "2026-07-31T17:03:15+00:00",
        "response": {"tool_results": [
            {"tool": "get_equity_quotes", "output": {"data": {"results": [
                {"quote": {"symbol": "NVDA", "last_trade_price": "182.50"}},
            ]}}},
            {"tool": "get_option_instruments", "output": {"data": {"instruments": [
                {"id": "inst-1", "chain_symbol": "NVDA", "type": "call",
                 "strike_price": "182.5000", "expiration_date": "2026-08-07"},
            ]}}},
            {"tool": "get_option_quotes", "output": {"data": {"results": [
                {"quote": {"instrument_id": "inst-1", "bid_price": "4.10",
                           "ask_price": "4.30", "volume": 900, "open_interest": 3000}},
            ]}}},
        ]},
    }))


def _decision(admissible: bool, qualified: list[str], volume_ratio: float = 2.0) -> dict:
    return {
        "schema_version": 1,
        "status": "OK",
        "decision_admissible": admissible,
        "qualified_symbols": qualified,
        "symbols": {symbol: {"volume_ratio": volume_ratio} for symbol in qualified},
    }


class PilotSampleTests(unittest.TestCase):
    def _run(self, root: Path, decision: dict, *, bars_fails: bool = False):
        log_root = root / "logs"
        trajectory_root = root / "trajectories"

        def fake_bars(symbols, project_root=None, **kwargs):
            if bars_fails:
                from execution.official_mcp_collector import OfficialCollectorError
                raise OfficialCollectorError("MCP_DOWN")
            # Production chunks the universe across several probes; the pilot
            # runner therefore receives a LIST of receipts, not one.
            return [_bars_receipt(root), _bars_receipt(root)]

        with patch("scripts.deterministic_slots.collect_universe_bars_probes", side_effect=fake_bars), \
             patch("scripts.deterministic_slots.evaluate_snapshot", return_value=dict(decision)), \
             patch("scripts.deterministic_slots.collect_official_raw_snapshot",
                   return_value=_option_receipt(root)), \
             patch("scripts.deterministic_slots.collect_fresh_option_quote_probe",
                   return_value=_option_receipt(root)):
            return run_pilot_sample(
                run_id="pilot-20260731-1003", scheduled=SCHEDULED, now=NOW,
                log_root=log_root, trajectory_root=trajectory_root,
                project_root=Path("."),
            ), log_root, trajectory_root

    def test_happy_path_opens_one_labelled_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, log_root, trajectory_root = self._run(root, _decision(True, ["NVDA"], 1.9))
            self.assertEqual("COMPLETED", summary["status"])
            self.assertIsNotNone(summary["opened_trajectory"])
            # Decision record + terminal summary exist (the reader contracts).
            self.assertTrue((log_root / "pilot-20260731-1003.decision.json").is_file())
            self.assertTrue((log_root / "pilot-20260731-1003.summary.json").is_file())
            events = list(trajectory_root.glob("*.json"))
            self.assertEqual(1, len(events))
            event = json.loads(events[0].read_text())
            self.assertEqual("CANDIDATE", event["event_type"])
            self.assertEqual(["BASE_18"], event["policy_labels"])  # vr=1.9 clears 1.8 only
            self.assertEqual(4.30, event["limit_price"])

    def test_no_policy_label_fires_means_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _log, trajectory_root = self._run(root, _decision(True, ["NVDA"], 1.6))
            self.assertEqual("COMPLETED", summary["status"])
            self.assertIsNone(summary["opened_trajectory"])
            self.assertEqual([], list(trajectory_root.glob("*.json")))
            open_step = next(s for s in summary["steps"] if s["step"] == "OPEN_CANDIDATE")
            self.assertEqual("NO_POLICY_LABEL_FIRED", open_step["skipped"])

    def test_not_admissible_means_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _log, trajectory_root = self._run(root, _decision(False, []))
            self.assertEqual("COMPLETED", summary["status"])
            self.assertEqual([], list(trajectory_root.glob("*.json")))

    def test_bars_probe_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, log_root, _t = self._run(root, _decision(True, ["NVDA"]), bars_fails=True)
            self.assertEqual("FAILED_CLOSED", summary["status"])
            # A terminal receipt still exists even on failure.
            self.assertTrue((log_root / "pilot-20260731-1003.summary.json").is_file())

    def test_second_run_refreshes_instead_of_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, _log, trajectory_root = self._run(root, _decision(True, ["NVDA"], 1.9))
            self.assertIsNotNone(first["opened_trajectory"])
            second, _log2, _t2 = self._run(root, _decision(True, ["NVDA"], 1.9))
            self.assertEqual("COMPLETED", second["status"])
            self.assertIsNone(second["opened_trajectory"])  # one candidate per day
            self.assertGreaterEqual(second["refreshed_events"], 1)  # QUOTE refresh happened
            kinds = {json.loads(p.read_text())["event_type"] for p in trajectory_root.glob("*.json")}
            self.assertEqual({"CANDIDATE", "QUOTE"}, kinds)


class CloseSummaryTests(unittest.TestCase):
    def test_close_summary_writes_receipt_with_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory)
            now = datetime(2026, 7, 31, 13, 5, tzinfo=PT)
            summary = run_close_summary(
                run_id="pilot-close-canary-20260731-1305", scheduled=now, now=now,
                log_root=log_root, project_root=Path("."),
            )
            self.assertEqual("COMPLETED", summary["status"])
            self.assertIn("slot_coverage", summary)
            self.assertIn("policy_label_counts", summary)
            self.assertIn("unresolved_incidents", summary)
            self.assertIn("safety", summary)
            self.assertEqual(0, summary["constraint_compliance"]["mcp_calls_made"])
            self.assertTrue((log_root / "pilot-close-canary-20260731-1305.summary.json").is_file())


if __name__ == "__main__":
    unittest.main()

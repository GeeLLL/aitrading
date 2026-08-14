from __future__ import annotations

import json
import tempfile
import unittest
from tempfile import TemporaryDirectory
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
        # direction is what picks the contract's side; a decision without it
        # must fail closed rather than open an arbitrary call or put.
        "symbols": {symbol: {"volume_ratio": volume_ratio, "direction": "CALL"}
                    for symbol in qualified},
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
            self.assertIsNotNone(summary["opened_trajectories"])
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
            self.assertEqual([], summary["opened_trajectories"])
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
            self.assertIsNotNone(first["opened_trajectories"])
            second, _log2, _t2 = self._run(root, _decision(True, ["NVDA"], 1.9))
            self.assertEqual("COMPLETED", second["status"])
            self.assertEqual([], second["opened_trajectories"])  # one per SYMBOL per day
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


class HorizonObservabilityTests(unittest.TestCase):
    """Both trajectories opened before 2026-08-13 are permanently open because
    their holding horizon fell after the day's last slot: AMD opened 10:43 came
    due at 11:43 with sampling stopping at 11:23, and SOFI opened 10:23 came due
    at 11:23:05, five seconds after the final slot fired. A position that cannot
    be observed reaching its horizon yields no outcome AND consumes the day's
    one candidate, so it must never be opened."""

    def _at(self, hour, minute, second=3):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 8, 14, hour, minute, second,
                        tzinfo=ZoneInfo("America/Los_Angeles"))

    def test_a_candidate_is_refused_once_its_horizon_outruns_the_schedule(self):
        from pathlib import Path
        from monitoring.daily_schedule import LAST_PILOT_SLOT
        from research.trajectory_recorder import TARGET_HORIZON_MINUTES
        from scripts.deterministic_slots import _horizon_is_observable

        last_hour, last_minute = LAST_PILOT_SLOT
        cutoff = last_hour * 60 + last_minute - TARGET_HORIZON_MINUTES
        self.assertTrue(_horizon_is_observable(
            self._at(cutoff // 60, cutoff % 60), Path(".")))
        self.assertFalse(_horizon_is_observable(
            self._at((cutoff + 20) // 60, (cutoff + 20) % 60), Path(".")))

    def test_the_cutoff_is_not_decided_by_launch_lag(self):
        """Slots fire seconds after their scheduled minute. Comparing raw
        timestamps let a 3-second lag disqualify a whole slot — exactly what
        stranded SOFI. Eligibility must not change within a slot's own minute."""
        from pathlib import Path
        from monitoring.daily_schedule import LAST_PILOT_SLOT
        from research.trajectory_recorder import TARGET_HORIZON_MINUTES
        from scripts.deterministic_slots import _horizon_is_observable

        last_hour, last_minute = LAST_PILOT_SLOT
        cutoff = last_hour * 60 + last_minute - TARGET_HORIZON_MINUTES
        verdicts = {
            _horizon_is_observable(self._at(cutoff // 60, cutoff % 60, second), Path("."))
            for second in (0, 3, 30, 59)
        }
        self.assertEqual(len(verdicts), 1, "eligibility flipped inside one slot minute")

    def test_the_schedule_can_observe_a_position_opened_at_the_cutoff(self):
        """The real invariant: some slot must be scheduled at or after the
        horizon of a position opened at the last eligible slot."""
        from monitoring.daily_schedule import DAILY_SLOTS, LAST_PILOT_SLOT
        from research.trajectory_recorder import TARGET_HORIZON_MINUTES

        last_hour, last_minute = LAST_PILOT_SLOT
        cutoff = last_hour * 60 + last_minute - TARGET_HORIZON_MINUTES
        horizon = cutoff + TARGET_HORIZON_MINUTES
        observers = [
            h * 60 + m for (h, m), (kind, _s) in DAILY_SLOTS.items()
            if kind == "PILOT_SAMPLE" and h * 60 + m >= horizon
        ]
        self.assertTrue(observers, "no slot can observe the last eligible candidate")


class FillWindowProbeTests(unittest.TestCase):
    """A simulated entry requires a LATER quote showing ask <= the limit before
    the 60-second window shuts. Slots are 20 minutes apart, so the next quote a
    trajectory ever saw arrived ~433s after its candidate — seven times outside
    the window. No fill could ever be simulated, which is why every EOD report
    read "0 filled/exited" and policy P&L was structurally $0.00 rather than
    merely small. The confirming quote has to happen in the same run."""

    def _candidate(self, deadline_in_seconds: float) -> dict:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        return {
            "trajectory_id": "T-1",
            "instrument_id": "inst-1",
            "decision_time": now.isoformat(),
            "target_horizon_minutes": 60,
            "underlying": "SOFI",
            "option_type": "CALL",
            "strike": 18.5,
            "limit_price": 0.61,
            "ask": 0.61,
            "quote_received_at": now.isoformat(),
            "fill_window_deadline": (now + timedelta(seconds=deadline_in_seconds)).isoformat(),
        }

    def _receipt(self, root: Path, ask: str):
        from execution.raw_data_vault import RawDataVault
        from datetime import timedelta
        received = datetime.now(timezone.utc)
        return RawDataVault(root).store(
            source="ROBINHOOD_OFFICIAL_MCP",
            request={"schema_version": 1, "symbol": "SOFI", "tool_calls": []},
            response={"tool_results": [{
                "tool": "get_option_quotes",
                "output": {"data": {"results": [{"quote": {
                    "instrument_id": "inst-1", "ask_price": ask, "bid_price": "0.58",
                }}]}},
            }]},
            source_updated_at=received - timedelta(seconds=1),
            received_at=received,
        )

    def test_the_confirming_quote_is_taken_inside_the_window(self):
        from scripts.deterministic_slots import (
            FILL_PROBE_BUDGET_SECONDS, _observe_fill_window,
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            steps = []
            candidate = self._candidate(FILL_PROBE_BUDGET_SECONDS)   # fire immediately
            with patch("scripts.deterministic_slots._collect_quote_probe",
                       return_value=self._receipt(root, "0.60")):
                _observe_fill_window(event=candidate, trajectory_root=root / "traj",
                                     project_root=root, stamp=steps.append)
            self.assertEqual(steps[0]["step"], "FILL_WINDOW_PROBE")
            self.assertTrue(steps[0]["ok"], steps[0])
            self.assertTrue(steps[0]["inside_window"])
            self.assertLess(steps[0]["seconds_into_window"], 60)
            written = list((root / "traj").glob("*"))
            self.assertTrue(written, "no confirming quote event was recorded")

    def test_an_already_closed_window_is_recorded_not_probed(self):
        from scripts.deterministic_slots import _observe_fill_window
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            steps = []
            with patch("scripts.deterministic_slots._collect_quote_probe") as probe:
                _observe_fill_window(event=self._candidate(-10), trajectory_root=root,
                                     project_root=root, stamp=steps.append)
            probe.assert_not_called()
            self.assertFalse(steps[0]["ok"])
            self.assertEqual(steps[0]["error"], "WINDOW_CLOSED_BEFORE_PROBE")

    def test_a_candidate_without_a_window_fails_closed(self):
        from scripts.deterministic_slots import _observe_fill_window
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            steps = []
            with patch("scripts.deterministic_slots._collect_quote_probe") as probe:
                _observe_fill_window(event={"instrument_id": "x"}, trajectory_root=root,
                                     project_root=root, stamp=steps.append)
            probe.assert_not_called()
            self.assertEqual(steps[0]["error"], "NO_FILL_WINDOW_ON_CANDIDATE")

    def test_the_budget_leaves_room_for_the_probes_own_round_trip(self):
        """A wait paced without allowing for the probe lands the quote after the
        window it was meant to fall inside — that cost a met limit on 07-30."""
        from scripts.deterministic_slots import FILL_PROBE_BUDGET_SECONDS
        from scripts.eod_report import DEFAULT_FILL_WINDOW_SECONDS
        self.assertGreaterEqual(FILL_PROBE_BUDGET_SECONDS, 15)
        self.assertLess(FILL_PROBE_BUDGET_SECONDS, DEFAULT_FILL_WINDOW_SECONDS)


class MultipleCandidatesTests(unittest.TestCase):
    """The one-per-DAY cap was inherited from capital allocation, but a read-only
    shadow allocates no capital. It bought nothing and cost data: on 2026-08-03
    four signals cleared BASE_18 and three were discarded, leaving a single
    trajectory for the whole period. The cap is now one per SYMBOL per day."""

    def test_every_qualifying_symbol_opens(self):
        from scripts import deterministic_slots as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        block = source[source.index("# 4. Open new candidates"):source.index("# 5. Daily calibration")]
        self.assertIn("for target_symbol in fresh:", block)
        self.assertNotIn("has_open_candidate", block)

    def test_a_symbol_already_open_today_is_not_reopened(self):
        from scripts import deterministic_slots as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        block = source[source.index("# 4. Open new candidates"):source.index("# 5. Daily calibration")]
        self.assertIn("symbols_open_today", block)
        self.assertIn("ALL_QUALIFIED_ALREADY_OPEN_TODAY", block)

    def test_each_opened_candidate_gets_its_own_fill_window_probe(self):
        """Otherwise the extra trajectories are unfillable exactly like the old
        single one, and more positions still means zero P&L."""
        from scripts import deterministic_slots as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        block = source[source.index("for target_symbol in fresh:"):source.index("# 5. Daily calibration")]
        self.assertIn("_observe_fill_window(", block)

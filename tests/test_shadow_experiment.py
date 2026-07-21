from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from journal.shadow_experiment import build_shadow_experiment_report
from journal.shadow_recorder import ShadowEventType, ShadowSessionRecorder


class ShadowExperimentTests(unittest.TestCase):
    def test_pilot_data_cannot_pass_experiment_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = ShadowSessionRecorder("strategy_v1.0", date(2026, 7, 17), root=Path(directory))
            recorder.append(ShadowEventType.SESSION_STARTED, {})
            recorder.append(
                ShadowEventType.DECISION_RECORDED,
                {"status": "PILOT_SIMULATED_ENTRY", "reasons": ["PILOT_NOT_STRATEGY_EVIDENCE"]},
            )
            recorder.append(ShadowEventType.ENTRY_WORKING, {})
            recorder.append(ShadowEventType.ENTRY_FILLED, {})
            recorder.append(ShadowEventType.EXITED, {"simulated_pnl_usd": "99"})
            report = build_shadow_experiment_report(root=directory, minimum_days=1, minimum_trades=0)
            self.assertFalse(report.gates["zero_pilot_decisions"])
            self.assertEqual(0, report.completed_trades)
            self.assertEqual("0", report.to_dict()["net_pnl_usd"])
            self.assertFalse(report.to_dict()["all_validation_gates_passed"])

    def test_no_logs_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_shadow_experiment_report(root=directory)

    def non_pilot_session(self, directory: str) -> None:
        recorder = ShadowSessionRecorder("strategy_v1.0", date(2026, 7, 17), root=Path(directory))
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        recorder.append(
            ShadowEventType.DECISION_RECORDED,
            {"status": "PILOT_SIMULATED_ENTRY", "reasons": []},
        )
        recorder.append(ShadowEventType.ENTRY_WORKING, {})
        recorder.append(ShadowEventType.ENTRY_FILLED, {})
        recorder.append(ShadowEventType.EXITED, {"simulated_pnl_usd": "12.60"})

    def test_non_pilot_session_without_authorization_record_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.non_pilot_session(directory)
            report = build_shadow_experiment_report(
                root=directory,
                minimum_days=1,
                minimum_trades=0,
                authorization_path=Path(directory) / "missing_authorization.json",
            )
            self.assertFalse(report.formal_shadow_authorized)
            self.assertEqual(1, report.unauthorized_sessions)
            self.assertEqual(0, report.completed_trades)
            self.assertEqual("0", report.to_dict()["net_pnl_usd"])
            self.assertFalse(report.gates["zero_unauthorized_sessions"])
            self.assertFalse(report.to_dict()["all_validation_gates_passed"])

    def test_verified_authorization_record_admits_non_pilot_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.non_pilot_session(directory)
            authorization = Path(directory) / "shadow_authorization.json"
            authorization.write_text(
                '{"authorized": true, "strategy_version": "strategy_v1.0", "approved_at": "2026-07-21T00:00:00+00:00"}',
                encoding="utf-8",
            )
            report = build_shadow_experiment_report(
                root=directory,
                minimum_days=1,
                minimum_trades=0,
                authorization_path=authorization,
            )
            self.assertTrue(report.formal_shadow_authorized)
            self.assertEqual(0, report.unauthorized_sessions)
            self.assertEqual(1, report.completed_trades)
            self.assertEqual("12.60", report.to_dict()["net_pnl_usd"])


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from journal.shadow_recorder import ShadowEventType, ShadowSessionRecorder
from journal.shadow_review import InvalidShadowLogError, review_shadow_day


DAY = date(2026, 7, 17)


class ShadowReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def recorder(self) -> ShadowSessionRecorder:
        return ShadowSessionRecorder("strategy_v1.0", DAY, root=self.root)

    def test_completed_no_trade_session_is_counted(self) -> None:
        recorder = self.recorder()
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        recorder.append(
            ShadowEventType.DECISION_RECORDED,
            {"status": "NO_TRADE", "reasons": ["MARKET_REGIME_MIXED"]},
        )
        recorder.append(ShadowEventType.SESSION_COMPLETED, {"trade_count": 0})
        review = review_shadow_day(DAY, root=self.root)
        self.assertEqual(1, review.completed_sessions)
        self.assertEqual(1, review.no_trade_decisions)
        self.assertTrue(review.to_dict()["strategy_evidence_eligible"])

    def test_exit_pnl_is_summed_only_from_exit_events(self) -> None:
        recorder = self.recorder()
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        recorder.append(ShadowEventType.ENTRY_WORKING, {})
        recorder.append(ShadowEventType.ENTRY_FILLED, {})
        recorder.append(
            ShadowEventType.EXITED,
            {"simulated_pnl_usd": "12.50"},
        )
        review = review_shadow_day(DAY, root=self.root)
        self.assertEqual(Decimal("12.50"), review.simulated_net_pnl_usd)
        self.assertEqual(1, review.simulated_exits)

    def test_non_terminal_log_is_not_evidence_eligible(self) -> None:
        recorder = self.recorder()
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        review = review_shadow_day(DAY, root=self.root)
        self.assertEqual(1, review.error_sessions)
        self.assertFalse(review.to_dict()["strategy_evidence_eligible"])

    def test_pilot_decision_is_never_strategy_evidence(self) -> None:
        recorder = self.recorder()
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        recorder.append(
            ShadowEventType.DECISION_RECORDED,
            {
                "status": "PILOT_SIMULATED_ENTRY",
                "reasons": ["PILOT_NOT_STRATEGY_EVIDENCE"],
            },
        )
        recorder.append(ShadowEventType.ENTRY_WORKING, {})
        recorder.append(ShadowEventType.ENTRY_UNFILLED, {})
        review = review_shadow_day(DAY, root=self.root)
        self.assertEqual(1, review.pilot_decisions)
        self.assertFalse(review.to_dict()["strategy_evidence_eligible"])

    def test_explicit_hard_rule_rejection_is_not_evidence_eligible(self) -> None:
        recorder = self.recorder()
        recorder.append(ShadowEventType.SESSION_STARTED, {})
        recorder.append(
            ShadowEventType.DECISION_RECORDED,
            {
                "status": "REJECTED",
                "reasons": ["ANY_REASON_WITHOUT_A_NAMING_CONVENTION"],
                "hard_rule_rejection": True,
            },
        )
        recorder.append(ShadowEventType.SESSION_COMPLETED, {"trade_count": 0})
        review = review_shadow_day(DAY, root=self.root)
        self.assertEqual(1, review.hard_rule_violations)
        self.assertFalse(review.to_dict()["strategy_evidence_eligible"])

    def test_broken_sequence_is_rejected(self) -> None:
        recorder = self.recorder()
        path = recorder.append(ShadowEventType.SESSION_STARTED, {})
        text = path.read_text(encoding="utf-8").replace('"sequence":1', '"sequence":2')
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(InvalidShadowLogError):
            review_shadow_day(DAY, root=self.root)


if __name__ == "__main__":
    unittest.main()

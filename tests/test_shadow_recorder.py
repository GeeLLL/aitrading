from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from journal.shadow_recorder import (
    ShadowEventType,
    ShadowSessionRecorder,
    ShadowSessionState,
)
from journal.writer import JournalValidationError


class ShadowRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.recorder = ShadowSessionRecorder(
            strategy_version="strategy_v1.0",
            session_date=date(2026, 7, 17),
            root=Path(self.temporary.name),
            session_id="test-session",
        )

    def test_complete_filled_session_is_ordered_and_durable(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_STARTED, {"mode": "PILOT"})
        self.recorder.append(ShadowEventType.DECISION_RECORDED, {"decision": "CALL"})
        self.recorder.append(ShadowEventType.ENTRY_WORKING, {"limit_price": Decimal("0.47")})
        self.recorder.append(ShadowEventType.ENTRY_FILLED, {"fill_price": Decimal("0.47")})
        self.recorder.append(ShadowEventType.POSITION_SNAPSHOT, {"bid": Decimal("0.50")})
        path = self.recorder.append(ShadowEventType.EXITED, {"exit_price": Decimal("0.50")})
        self.assertEqual(ShadowSessionState.COMPLETE, self.recorder.state)
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(6, len(lines))
        self.assertEqual(list(range(1, 7)), [json.loads(line)["sequence"] for line in lines])

    def test_unfilled_entry_completes_without_position(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_STARTED, {})
        self.recorder.append(ShadowEventType.ENTRY_WORKING, {"limit_price": "0.47"})
        self.recorder.append(ShadowEventType.ENTRY_UNFILLED, {"reason": "TIMEOUT"})
        self.assertEqual(ShadowSessionState.COMPLETE, self.recorder.state)

    def test_exit_before_fill_is_rejected(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_STARTED, {})
        with self.assertRaises(JournalValidationError):
            self.recorder.append(ShadowEventType.EXITED, {})

    def test_events_after_completion_are_rejected(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_STARTED, {})
        self.recorder.append(ShadowEventType.ENTRY_WORKING, {})
        self.recorder.append(ShadowEventType.ENTRY_UNFILLED, {})
        with self.assertRaises(JournalValidationError):
            self.recorder.append(ShadowEventType.POSITION_SNAPSHOT, {})

    def test_sensitive_fields_are_rejected_recursively(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_STARTED, {})
        with self.assertRaises(JournalValidationError):
            self.recorder.append(
                ShadowEventType.DECISION_RECORDED,
                {"raw": {"account_number": "forbidden"}},
            )

    def test_error_terminates_session(self) -> None:
        self.recorder.append(ShadowEventType.SESSION_ERROR, {"reason": "MCP_DISCONNECTED"})
        self.assertEqual(ShadowSessionState.ERROR, self.recorder.state)


if __name__ == "__main__":
    unittest.main()

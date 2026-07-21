from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitoring.scheduler_health import evaluate_start_ack, write_start_ack


SCHEDULED = datetime(2026, 7, 20, 13, 15, tzinfo=timezone.utc)


class SchedulerHealthTests(unittest.TestCase):
    def test_missing_ack_becomes_missed_after_grace(self) -> None:
        result = evaluate_start_ack(
            path="does-not-exist.json",
            scheduled_for=SCHEDULED,
            checked_at=SCHEDULED + timedelta(seconds=121),
        )
        self.assertFalse(result.healthy)
        self.assertEqual("SCHEDULED_RUN_MISSED", result.reason)

    def test_missing_ack_is_pending_during_grace(self) -> None:
        result = evaluate_start_ack(
            path="does-not-exist.json",
            scheduled_for=SCHEDULED,
            checked_at=SCHEDULED + timedelta(seconds=30),
        )
        self.assertEqual("START_ACK_PENDING", result.reason)

    def test_valid_ack_is_atomic_and_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_start_ack(
                run_id="shadow-20260720-0615",
                scheduled_for=SCHEDULED,
                acknowledged_at=SCHEDULED + timedelta(seconds=4),
                directory=directory,
            )
            result = evaluate_start_ack(
                path=path,
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=5),
            )
            self.assertTrue(result.healthy)
            self.assertEqual(4, result.delay_seconds)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_mismatched_schedule_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ack.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "expected",
                "status": "STARTED",
                "scheduled_for": (SCHEDULED + timedelta(minutes=1)).isoformat(),
                "acknowledged_at": SCHEDULED.isoformat(),
            }))
            result = evaluate_start_ack(
                path=path,
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED,
            )
            self.assertEqual("START_ACK_SCHEDULE_MISMATCH", result.reason)

    def test_late_ack_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_start_ack(
                run_id="late",
                scheduled_for=SCHEDULED,
                acknowledged_at=SCHEDULED + timedelta(seconds=121),
                directory=directory,
            )
            result = evaluate_start_ack(
                path=path,
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=122),
            )
            self.assertEqual("START_ACK_LATE", result.reason)

    def test_wrong_run_id_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_start_ack(
                run_id="wrong",
                scheduled_for=SCHEDULED,
                acknowledged_at=SCHEDULED,
                directory=directory,
            )
            result = evaluate_start_ack(
                path=path,
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED,
                expected_run_id="expected",
            )
            self.assertEqual("START_ACK_RUN_ID_MISMATCH", result.reason)

    def test_non_started_status_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ack.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "expected",
                "status": "FAILED",
                "scheduled_for": SCHEDULED.isoformat(),
                "acknowledged_at": SCHEDULED.isoformat(),
            }))
            result = evaluate_start_ack(
                path=path,
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED,
                expected_run_id="expected",
            )
            self.assertEqual("START_ACK_INVALID", result.reason)


if __name__ == "__main__":
    unittest.main()

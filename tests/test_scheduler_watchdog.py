from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitoring.scheduler_health import write_start_ack
from monitoring.scheduler_watchdog import (
    catch_up_policy,
    check_expected_run,
    register_expected_run,
    scan_expected_runs,
)


SCHEDULED = datetime(2026, 7, 20, 13, 15, tzinfo=timezone.utc)


class SchedulerWatchdogTests(unittest.TestCase):
    def test_pending_does_not_create_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = check_expected_run(
                run_id="pending",
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=30),
                ack_directory=directory,
                incident_directory=directory,
            )
            self.assertEqual("START_ACK_PENDING", result.health.reason)
            self.assertIsNone(result.incident_path)

    def test_missed_run_creates_durable_fail_closed_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = check_expected_run(
                run_id="missed",
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=121),
                ack_directory=directory,
                incident_directory=directory,
            )
            self.assertIsNotNone(result.incident_path)
            payload = json.loads(Path(result.incident_path).read_text())
            self.assertEqual("CRITICAL", payload["severity"])
            self.assertTrue(payload["new_entries_blocked"])
            self.assertEqual("SCHEDULED_RUN_MISSED", payload["health"]["reason"])
            self.assertEqual("DO_NOT_BACKFILL_MARKET_SAMPLE", payload["catch_up_policy"])
            self.assertTrue((Path(directory) / "alerts/missed.alert.json").is_file())

    def test_repeated_poll_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = check_expected_run(
                run_id="missed",
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=121),
                ack_directory=directory,
                incident_directory=directory,
            )
            initial = Path(first.incident_path).read_text()
            second = check_expected_run(
                run_id="missed",
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(minutes=5),
                ack_directory=directory,
                incident_directory=directory,
            )
            self.assertEqual(first.incident_path, second.incident_path)
            self.assertEqual(initial, Path(second.incident_path).read_text())
            self.assertEqual(1, len(list((Path(directory) / "alerts").glob("*.alert.json"))))

    def test_valid_ack_does_not_create_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            write_start_ack(
                run_id="valid",
                scheduled_for=SCHEDULED,
                acknowledged_at=SCHEDULED + timedelta(seconds=5),
                directory=directory,
            )
            result = check_expected_run(
                run_id="valid",
                scheduled_for=SCHEDULED,
                checked_at=SCHEDULED + timedelta(seconds=30),
                ack_directory=directory,
                incident_directory=directory,
            )
            self.assertTrue(result.health.healthy)
            self.assertIsNone(result.incident_path)

    def test_registered_expectation_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected"
            acks = Path(directory) / "acks"
            incidents = Path(directory) / "incidents"
            register_expected_run(
                run_id="missing",
                scheduled_for=SCHEDULED,
                directory=expected,
            )
            results = scan_expected_runs(
                checked_at=SCHEDULED + timedelta(seconds=121),
                expectation_directory=expected,
                ack_directory=acks,
                incident_directory=incidents,
            )
            self.assertEqual(1, len(results))
            self.assertEqual("SCHEDULED_RUN_MISSED", results[0].health.reason)
            self.assertTrue((incidents / "missing.scheduler-incident.json").is_file())

    def test_malformed_expectation_creates_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected"
            expected.mkdir()
            (expected / "broken.expected.json").write_text("not-json")
            results = scan_expected_runs(
                checked_at=SCHEDULED,
                expectation_directory=expected,
                ack_directory=Path(directory) / "acks",
                incident_directory=Path(directory) / "incidents",
            )
            self.assertEqual(1, len(results))
            self.assertEqual("SCHEDULER_EXPECTATION_INVALID", results[0].health.reason)
            self.assertTrue((Path(directory) / "incidents/broken.scheduler-incident.json").is_file())

    def test_retired_incident_expectation_is_not_realerted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected"
            expected.mkdir()
            (expected / "retired.expected.json").write_text(json.dumps({
                "schema_version": 1,
                "run_id": "retired",
                "scheduled_for": SCHEDULED.isoformat(),
                "status": "RETIRED_INCIDENT_RETAINED",
            }))
            results = scan_expected_runs(
                checked_at=SCHEDULED + timedelta(days=1),
                expectation_directory=expected,
                ack_directory=Path(directory) / "acks",
                incident_directory=Path(directory) / "incidents",
            )
            self.assertEqual((), results)

    def test_close_summary_has_local_only_catch_up_policy(self) -> None:
        self.assertEqual(
            "LOCAL_LOG_SUMMARY_ONLY_MARK_INCOMPLETE",
            catch_up_policy("pilot-close-canary-20260721-1305"),
        )

    def test_end_to_end_expectation_ack_and_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected"
            acks = Path(directory) / "acks"
            incidents = Path(directory) / "incidents"
            register_expected_run(run_id="canary", scheduled_for=SCHEDULED, directory=expected)
            write_start_ack(
                run_id="canary",
                scheduled_for=SCHEDULED,
                acknowledged_at=SCHEDULED + timedelta(seconds=3),
                directory=acks,
            )
            results = scan_expected_runs(
                checked_at=SCHEDULED + timedelta(seconds=121),
                expectation_directory=expected,
                ack_directory=acks,
                incident_directory=incidents,
            )
            self.assertEqual(1, len(results))
            self.assertTrue(results[0].health.healthy)
            self.assertFalse(incidents.exists())


if __name__ == "__main__":
    unittest.main()

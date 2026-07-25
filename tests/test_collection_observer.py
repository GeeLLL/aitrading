"""Tests for the single, pure collection observer (P1-C consolidation).

Locks in: observe-only status computation (no side effects), the market-hours
gate coming from the corrected calendar, and `ensure_day_registered` closing the
whole-day-asleep gap.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring.collection_observer import (
    ensure_day_registered,
    observe_collection,
    render_report,
)
from monitoring.daily_schedule import expected_runs_for_date
from monitoring.scheduler_health import write_start_ack
from monitoring.scheduler_watchdog import scan_expected_runs

PT = ZoneInfo("America/Los_Angeles")


def _dirs(root: Path):
    return dict(
        project_root=root,
        expectation_directory=root / "logs/scheduler/expected",
        ack_directory=root / "logs/scheduler",
        incident_directory=root / "logs/incidents",
        worker_directory=root / "logs/launchd_worker",
    )


def _ack(root: Path, run_id: str, scheduled: datetime) -> None:
    write_start_ack(
        run_id=run_id,
        scheduled_for=scheduled,
        acknowledged_at=scheduled,
        directory=root / "logs/scheduler",
    )


class ObserveCollectionTests(unittest.TestCase):
    def test_closed_day_reports_market_closed_no_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saturday = datetime(2026, 7, 25, 8, 0, tzinfo=PT)
            status = observe_collection(saturday, **_dirs(root))
            self.assertFalse(status.market_open)
            self.assertEqual((), status.slots)
            self.assertIn("CLOSED", render_report(status))

    def test_mixed_ran_missed_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 7, 22, 7, 10, tzinfo=PT)  # Wednesday
            runs = dict(expected_runs_for_date(now.date()))
            # Mark the 06:10 canary as run.
            for run_id, scheduled in runs.items():
                if scheduled.hour == 6 and scheduled.minute == 10:
                    _ack(root, run_id, scheduled)
            status = observe_collection(now, **_dirs(root))
            self.assertTrue(status.market_open)
            self.assertEqual(1, len(status.ran))
            # 06:35 and 07:03 are past + grace with no ack -> missed.
            self.assertGreaterEqual(len(status.missed), 2)
            # Later slots are still pending.
            self.assertTrue(status.pending)
            self.assertFalse(status.healthy)

    def test_all_ran_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # After the close, with every slot acked -> healthy.
            end_of_day = datetime(2026, 7, 22, 14, 0, tzinfo=PT)
            for run_id, scheduled in expected_runs_for_date(end_of_day.date()):
                _ack(root, run_id, scheduled)
            status = observe_collection(end_of_day, **_dirs(root))
            self.assertEqual(0, len(status.missed))
            self.assertEqual(0, len(status.pending))
            self.assertTrue(status.healthy)

    def test_unresolved_incident_makes_status_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incidents = root / "logs/incidents"
            incidents.mkdir(parents=True, exist_ok=True)
            (incidents / "some-run.scheduler-incident.json").write_text(
                json.dumps({"schema_version": 1, "run_id": "some-run"}), encoding="utf-8"
            )
            # A fully-acked day is still DEGRADED while an incident is unresolved.
            end_of_day = datetime(2026, 7, 22, 14, 0, tzinfo=PT)
            for run_id, scheduled in expected_runs_for_date(end_of_day.date()):
                _ack(root, run_id, scheduled)
            status = observe_collection(end_of_day, **_dirs(root))
            self.assertIn("some-run", status.unresolved_incidents)
            self.assertFalse(status.healthy)

    def test_observer_writes_nothing(self) -> None:
        # Pure: observing must not create the expectation directory or any file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 7, 22, 7, 10, tzinfo=PT)
            observe_collection(now, **_dirs(root))
            self.assertFalse((root / "logs/scheduler/expected").exists())


class EnsureDayRegisteredTests(unittest.TestCase):
    def test_registers_full_day_on_market_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 7, 22, 6, 5, tzinfo=PT)
            count = ensure_day_registered(now, project_root=root)
            expected = expected_runs_for_date(now.date())
            self.assertEqual(len(expected), count)
            files = list((root / "logs/scheduler/expected").glob("*.expected.json"))
            self.assertEqual(len(expected), len(files))

    def test_noop_on_weekend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saturday = datetime(2026, 7, 25, 6, 5, tzinfo=PT)
            self.assertEqual(0, ensure_day_registered(saturday, project_root=root))
            self.assertFalse((root / "logs/scheduler/expected").exists())

    def test_noop_on_holiday(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good_friday = datetime(2026, 4, 3, 6, 5, tzinfo=PT)
            self.assertEqual(0, ensure_day_registered(good_friday, project_root=root))

    def test_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 7, 22, 6, 5, tzinfo=PT)
            first = ensure_day_registered(now, project_root=root)
            second = ensure_day_registered(now, project_root=root)
            self.assertEqual(first, second)
            files = list((root / "logs/scheduler/expected").glob("*.expected.json"))
            self.assertEqual(first, len(files))  # no duplicates

    def test_registration_closes_whole_day_asleep_gap_for_watchdog(self) -> None:
        # The watchdog only checks REGISTERED expectations. If the Mac slept
        # through every worker slot, nothing registered the day and the watchdog
        # had nothing to flag -> silent miss. ensure_day_registered (wired into
        # the watchdog tick) back-registers the day so the watchdog flags it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected_dir = root / "logs/scheduler/expected"
            ack_dir = root / "logs/scheduler"
            incident_dir = root / "logs/incidents"
            now = datetime(2026, 7, 22, 12, 0, tzinfo=PT)  # woke at noon, nothing ran

            # Before registration: the watchdog scans an empty dir -> nothing.
            before = scan_expected_runs(
                checked_at=now,
                expectation_directory=expected_dir,
                ack_directory=ack_dir,
                incident_directory=incident_dir,
            )
            self.assertEqual((), before)

            # After registration: the past morning slots are now visible misses.
            ensure_day_registered(now, project_root=root)
            after = scan_expected_runs(
                checked_at=now,
                expectation_directory=expected_dir,
                ack_directory=ack_dir,
                incident_directory=incident_dir,
            )
            flagged = [r for r in after if not r.health.healthy and r.health.reason != "START_ACK_PENDING"]
            self.assertTrue(flagged)  # the slept-through morning is no longer silent


if __name__ == "__main__":
    unittest.main()

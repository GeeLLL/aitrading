from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from monitoring.worker_reaper import (
    DIED_NO_SUMMARY,
    FINISHED_STALE,
    INVALID_RECORD,
    OVERDUE_KILL,
    PID_REUSED,
    RUNNING_OK,
    classify_worker,
    reap_overdue_workers,
)


NOW = datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc)


def record(kind: str = "PILOT_SAMPLE", age_seconds: int = 0) -> dict:
    return {
        "schema_version": 1,
        "pid": 12345,
        "run_id": "pilot-20260727-0703",
        "kind": kind,
        "started_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "summary_path": "/nonexistent/summary.json",
    }


class ClassifyWorkerTests(unittest.TestCase):
    def test_recent_worker_is_left_alone(self):
        verdict = classify_worker(
            record(age_seconds=300), now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, RUNNING_OK)

    def test_pilot_over_deadline_and_alive_is_killed(self):
        verdict = classify_worker(
            record(age_seconds=1021), now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, OVERDUE_KILL)

    def test_canary_deadline_is_tighter(self):
        verdict = classify_worker(
            record(kind="CANARY", age_seconds=800), now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, OVERDUE_KILL)

    def test_canary_within_deadline_runs_on(self):
        verdict = classify_worker(
            record(kind="CANARY", age_seconds=700), now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, RUNNING_OK)

    def test_finished_worker_record_is_just_stale(self):
        verdict = classify_worker(
            record(age_seconds=5000), now=NOW, summary_exists=True,
            process_alive=False, cmdline="",
        )
        self.assertEqual(verdict, FINISHED_STALE)

    def test_dead_process_without_summary_is_flagged(self):
        verdict = classify_worker(
            record(age_seconds=2000), now=NOW, summary_exists=False,
            process_alive=False, cmdline="",
        )
        self.assertEqual(verdict, DIED_NO_SUMMARY)

    def test_reused_pid_is_never_killed(self):
        verdict = classify_worker(
            record(age_seconds=2000), now=NOW, summary_exists=False,
            process_alive=True, cmdline="/usr/bin/some-other-daemon",
        )
        self.assertEqual(verdict, PID_REUSED)

    def test_malformed_record_fails_closed_as_invalid(self):
        for bad in ({}, {"pid": "x", "started_at": "nope"}, {"pid": 1, "started_at": "2026-07-27T00:00:00"}):
            self.assertEqual(
                classify_worker(bad, now=NOW, summary_exists=False, process_alive=False, cmdline=""),
                INVALID_RECORD,
            )


class ReapOverdueWorkersTests(unittest.TestCase):
    def test_dead_worker_files_incident_and_removes_pid_record(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sched = root / "logs/scheduler"
            incidents = root / "logs/incidents"
            sched.mkdir(parents=True)
            payload = record(age_seconds=5000)
            payload["pid"] = 99999999  # certainly not alive
            (sched / "pilot-20260727-0703.pid").write_text(json.dumps(payload), encoding="utf-8")

            actions = reap_overdue_workers(
                NOW, project_root=root, scheduler_dir=sched, incident_dir=incidents,
            )

            self.assertEqual([a["verdict"] for a in actions], [DIED_NO_SUMMARY])
            self.assertFalse((sched / "pilot-20260727-0703.pid").exists())
            incident = incidents / "pilot-20260727-0703-hung.scheduler-incident.json"
            self.assertTrue(incident.is_file())
            body = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(body["incident_type"], "WORKER_DIED_NO_SUMMARY")
            self.assertEqual(body["catch_up_policy"], "DO_NOT_BACKFILL_MARKET_SAMPLE")
            alert = incidents / "alerts" / "pilot-20260727-0703-hung.alert.json"
            self.assertTrue(alert.is_file())

    def test_finished_worker_record_is_cleaned_without_incident(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sched = root / "logs/scheduler"
            incidents = root / "logs/incidents"
            sched.mkdir(parents=True)
            summary = root / "summary.json"
            summary.write_text("{}", encoding="utf-8")
            payload = record(age_seconds=5000)
            payload["summary_path"] = str(summary)
            (sched / "pilot-20260727-0703.pid").write_text(json.dumps(payload), encoding="utf-8")

            actions = reap_overdue_workers(
                NOW, project_root=root, scheduler_dir=sched, incident_dir=incidents,
            )

            self.assertEqual([a["verdict"] for a in actions], [FINISHED_STALE])
            self.assertFalse((sched / "pilot-20260727-0703.pid").exists())
            self.assertFalse(incidents.exists())

    def test_incident_write_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sched = root / "logs/scheduler"
            incidents = root / "logs/incidents"
            sched.mkdir(parents=True)
            payload = record(age_seconds=5000)
            payload["pid"] = 99999999
            pid_file = sched / "pilot-20260727-0703.pid"
            pid_file.write_text(json.dumps(payload), encoding="utf-8")
            reap_overdue_workers(NOW, project_root=root, scheduler_dir=sched, incident_dir=incidents)
            first = (incidents / "pilot-20260727-0703-hung.scheduler-incident.json").read_text(encoding="utf-8")
            # Same record shows up again (e.g. re-created manually): no rewrite.
            pid_file.write_text(json.dumps(payload), encoding="utf-8")
            reap_overdue_workers(NOW, project_root=root, scheduler_dir=sched, incident_dir=incidents)
            second = (incidents / "pilot-20260727-0703-hung.scheduler-incident.json").read_text(encoding="utf-8")
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

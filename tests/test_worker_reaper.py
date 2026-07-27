from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import monitoring.worker_reaper as reaper_module
from monitoring.worker_reaper import (
    _kill_child_group,
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

    def test_deadline_anchors_to_scheduled_time_not_late_start(self):
        # Fired 170s late (admitted by the 180s freshness guard): the deadline
        # must still count from the SCHEDULED time, or the reap could land
        # after the next slot fires and cost a second sample.
        payload = record(age_seconds=860)  # started 860s ago...
        payload["scheduled_for"] = (NOW - timedelta(seconds=1030)).isoformat()  # ...scheduled 1030s ago
        verdict = classify_worker(
            payload, now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, OVERDUE_KILL)

    def test_unparseable_scheduled_for_falls_back_to_started_at(self):
        payload = record(age_seconds=300)
        payload["scheduled_for"] = "not-a-timestamp"
        verdict = classify_worker(
            payload, now=NOW, summary_exists=False,
            process_alive=True, cmdline="python3 scripts/launchd_shadow_worker.py",
        )
        self.assertEqual(verdict, RUNNING_OK)


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

    def test_orphaned_cli_child_group_is_killed_on_parent_death(self):
        # Parent crashed (record left behind), CLI child still alive: the reap
        # must kill the recorded child GROUP, or it keeps collecting with no
        # timeout at all.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sched = root / "logs/scheduler"
            incidents = root / "logs/incidents"
            sched.mkdir(parents=True)
            payload = record(age_seconds=5000)
            payload["pid"] = 99999999          # parent gone
            payload["child_pid"] = 88888888    # child "alive" (mocked)
            (sched / "pilot-20260727-0703.pid").write_text(json.dumps(payload), encoding="utf-8")

            killed_groups: list[int] = []
            real_cmdline = reaper_module._process_cmdline

            def fake_cmdline(pid: int):
                if pid == 88888888:
                    return True, "/Users/ge/.local/bin/claude -p --output-format stream-json"
                return real_cmdline(pid)

            def fake_killpg(pgid, sig):
                killed_groups.append(pgid)
                raise ProcessLookupError  # first signal "kills" it instantly

            with mock.patch.object(reaper_module, "_process_cmdline", side_effect=fake_cmdline), \
                 mock.patch.object(reaper_module.os, "killpg", side_effect=fake_killpg):
                actions = reap_overdue_workers(
                    NOW, project_root=root, scheduler_dir=sched, incident_dir=incidents,
                )

            self.assertEqual([a["verdict"] for a in actions], [DIED_NO_SUMMARY])
            self.assertIn(88888888, killed_groups)

    def test_child_group_with_foreign_cmdline_is_never_killed(self):
        killed: list[int] = []
        with mock.patch.object(
            reaper_module, "_process_cmdline",
            return_value=(True, "/usr/libexec/some-system-daemon"),
        ), mock.patch.object(reaper_module.os, "killpg", side_effect=lambda *a: killed.append(a)):
            result = _kill_child_group({"child_pid": 4242})
        self.assertFalse(result)
        self.assertEqual(killed, [])

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

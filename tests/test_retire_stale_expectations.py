from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.retire_stale_expectations import resolve_incidents, retire_expectations


CUTOFF = date(2026, 7, 27)


def expectation(run_id: str, scheduled_for: str, status: str = "EXPECTED") -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "scheduled_for": scheduled_for,
        "status": status,
    }


class RetireExpectationsTests(unittest.TestCase):
    def test_past_day_expected_entries_are_retired_in_place(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            past = directory / "pilot-20260724-0703.expected.json"
            past.write_text(json.dumps(
                expectation("pilot-20260724-0703", "2026-07-24T14:03:00+00:00")
            ), encoding="utf-8")

            count = retire_expectations(directory, CUTOFF, dry_run=False)

            self.assertEqual(count, 1)
            payload = json.loads(past.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "RETIRED_INCIDENT_RETAINED")
            self.assertIn("retired_at", payload)
            self.assertTrue(past.exists(), "file must be kept, never deleted")

    def test_today_and_future_entries_are_untouched(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            today = directory / "pilot-20260727-0703.expected.json"
            today.write_text(json.dumps(
                expectation("pilot-20260727-0703", "2026-07-27T14:03:00+00:00")
            ), encoding="utf-8")

            count = retire_expectations(directory, CUTOFF, dry_run=False)

            self.assertEqual(count, 0)
            payload = json.loads(today.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "EXPECTED")

    def test_already_retired_and_corrupt_files_are_skipped(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            retired = directory / "a.expected.json"
            retired.write_text(json.dumps(
                expectation("a", "2026-07-20T14:03:00+00:00", status="RETIRED_INCIDENT_RETAINED")
            ), encoding="utf-8")
            corrupt = directory / "b.expected.json"
            corrupt.write_text("{broken", encoding="utf-8")

            count = retire_expectations(directory, CUTOFF, dry_run=False)

            self.assertEqual(count, 0)
            self.assertEqual(corrupt.read_text(encoding="utf-8"), "{broken")


class ResolveIncidentsTests(unittest.TestCase):
    def test_past_unresolved_incident_gets_explicit_resolution(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            incident = directory / "pilot-20260724-0703.scheduler-incident.json"
            incident.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "pilot-20260724-0703",
                "detected_at": "2026-07-24T18:36:45+00:00",
            }), encoding="utf-8")

            count = resolve_incidents(directory, CUTOFF, "root cause text", "owner", dry_run=False)

            self.assertEqual(count, 1)
            payload = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(payload["resolution"]["status"], "RESOLVED_ROOT_CAUSE_DOCUMENTED")
            self.assertEqual(payload["resolution"]["root_cause"], "root cause text")
            self.assertTrue(incident.exists())

    def test_already_resolved_incidents_are_not_rewritten(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            incident = directory / "x.scheduler-incident.json"
            incident.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "pilot-20260720-0703",
                "detected_at": "2026-07-20T18:00:00+00:00",
                "resolution": {"status": "ALREADY_DONE"},
            }), encoding="utf-8")

            count = resolve_incidents(directory, CUTOFF, "rc", "owner", dry_run=False)

            self.assertEqual(count, 0)
            payload = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(payload["resolution"]["status"], "ALREADY_DONE")

    def test_incident_day_comes_from_run_id_not_batch_reconcile_stamp(self):
        # Detected "today" but the run itself is from a past day: still resolvable.
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            incident = directory / "pilot-20260721-0823.scheduler-incident.json"
            incident.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "pilot-20260721-0823",
                "detected_at": "2026-07-27T01:00:00+00:00",
            }), encoding="utf-8")

            count = resolve_incidents(directory, CUTOFF, "rc", "owner", dry_run=False)

            self.assertEqual(count, 1)

    def test_todays_incident_is_never_touched(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            incident = directory / "pilot-20260727-0703.scheduler-incident.json"
            incident.write_text(json.dumps({
                "schema_version": 1,
                "run_id": "pilot-20260727-0703",
                "detected_at": "2026-07-27T14:10:00+00:00",
            }), encoding="utf-8")

            count = resolve_incidents(directory, CUTOFF, "rc", "owner", dry_run=False)

            self.assertEqual(count, 0)
            self.assertNotIn("resolution", json.loads(incident.read_text(encoding="utf-8")))

    def test_corrupt_incident_files_fail_closed_untouched(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            corrupt = directory / "bad.scheduler-incident.json"
            corrupt.write_text("{nope", encoding="utf-8")

            count = resolve_incidents(directory, CUTOFF, "rc", "owner", dry_run=False)

            self.assertEqual(count, 0)
            self.assertEqual(corrupt.read_text(encoding="utf-8"), "{nope")


if __name__ == "__main__":
    unittest.main()

"""Collection resilience: a read-only sampling miss is a reliability event, not a
safety event. These lock in the P0-A reversals from the 2026-07-25 robustness
upgrade — an unresolved incident must stay *visible* but must no longer brick the
read-only collector, and resolved-only incidents are *archived*, never deleted.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.cleanup_expired_incidents import archive_resolved_incidents
from scripts.launchd_shadow_worker import _safety_ok
from monitoring.scheduler_watchdog import unresolved_incident_ids


def _write_incident(directory: Path, run_id: str, *, detected_at: datetime, resolution: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "incident_type": "SCHEDULER_START_FAILURE",
        "run_id": run_id,
        "detected_at": detected_at.astimezone(timezone.utc).isoformat(),
        "severity": "CRITICAL",
    }
    if resolution is not None:
        payload["resolution"] = resolution
    path = directory / f"{run_id}.scheduler-incident.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class SafetyGateIgnoresCollectionIncidentsTests(unittest.TestCase):
    def test_unresolved_incident_does_not_block_read_only_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            _write_incident(incidents, "missed-run", detected_at=datetime.now(timezone.utc))
            safe, status = _safety_ok(incident_directory=incidents)
            # The incident is still surfaced for visibility...
            self.assertIn("missed-run", status["unresolved_scheduler_incidents"])
            # ...but it no longer gates the read-only collector.
            self.assertTrue(safe)

    def test_no_test_mode_bypass_env_exists(self) -> None:
        # The removed SHADOW_TRADING_TEST_MODE bypass must not resurface.
        source = Path("scripts/launchd_shadow_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("SHADOW_TRADING_TEST_MODE", source)


class IncidentTtlRemovedTests(unittest.TestCase):
    def test_old_unresolved_incident_still_blocks(self) -> None:
        # No time-based auto-expiry: an incident does not become safe by aging.
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            _write_incident(
                incidents,
                "ancient-run",
                detected_at=datetime.now(timezone.utc) - timedelta(hours=72),
            )
            self.assertEqual(("ancient-run",), unresolved_incident_ids(incidents))

    def test_explicit_resolution_clears_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            _write_incident(
                incidents,
                "resolved-run",
                detected_at=datetime.now(timezone.utc),
                resolution={"status": "RESOLVED_BY_OWNER", "at": "2026-07-25T12:00:00Z"},
            )
            self.assertEqual((), unresolved_incident_ids(incidents))


class ArchiveOnlyCleanupTests(unittest.TestCase):
    def test_resolved_incident_is_archived_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            path = _write_incident(
                incidents,
                "done-run",
                detected_at=datetime.now(timezone.utc),
                resolution={"status": "RESOLVED_BY_OWNER"},
            )
            moved = archive_resolved_incidents(incident_dir=incidents)
            self.assertEqual(1, moved)
            self.assertFalse(path.exists())
            archived = incidents / "archive" / path.name
            self.assertTrue(archived.exists())  # preserved, not deleted

    def test_unresolved_incident_is_left_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            path = _write_incident(incidents, "active-run", detected_at=datetime.now(timezone.utc))
            moved = archive_resolved_incidents(incident_dir=incidents)
            self.assertEqual(0, moved)
            self.assertTrue(path.exists())
            self.assertFalse((incidents / "archive").exists())

    def test_corrupt_incident_is_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            corrupt = incidents / "broken.scheduler-incident.json"
            corrupt.write_text("{not json", encoding="utf-8")
            moved = archive_resolved_incidents(incident_dir=incidents)
            self.assertEqual(0, moved)
            # Corrupt files fail closed (still block) and must survive cleanup.
            self.assertTrue(corrupt.exists())
            self.assertEqual(("broken.scheduler-incident.json",), unresolved_incident_ids(incidents))

    def test_dry_run_moves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            incidents = Path(directory)
            path = _write_incident(
                incidents,
                "done-run",
                detected_at=datetime.now(timezone.utc),
                resolution={"status": "RESOLVED"},
            )
            moved = archive_resolved_incidents(incident_dir=incidents, dry_run=True)
            self.assertEqual(1, moved)  # reports it would archive one
            self.assertTrue(path.exists())  # but moves nothing
            self.assertFalse((incidents / "archive").exists())


if __name__ == "__main__":
    unittest.main()

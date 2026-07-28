from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from monitoring.authorization_watch import (
    ABSENT,
    acknowledge_authorization,
    check_authorization_record,
    fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)


def _write_authorization(root: Path, approved_at: str = "2026-07-28T16:00:00+00:00") -> Path:
    path = root / "state/shadow_authorization.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "authorized": True,
        "strategy_version": "strategy_v1.0",
        "approved_at": approved_at,
    }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


class AuthorizationWatchTests(unittest.TestCase):
    def test_absent_record_self_baselines_quietly(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = check_authorization_record(NOW, project_root=root)
            self.assertEqual(result["status"], "BASELINED_ABSENT")
            self.assertFalse(result["changed"])
            self.assertFalse((root / "logs/incidents").exists())

    def test_authorization_appearing_after_baseline_raises_critical_incident(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            check_authorization_record(NOW, project_root=root)  # baseline: ABSENT
            _write_authorization(root)                          # forged or owner-made

            result = check_authorization_record(NOW, project_root=root)

            self.assertEqual(result["status"], "CHANGED")
            self.assertTrue(result["changed"])
            incident = Path(str(result["incident_path"]))
            self.assertTrue(incident.is_file())
            payload = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(payload["incident_type"], "GOVERNED_FILE_CHANGED")
            self.assertEqual(payload["severity"], "CRITICAL")
            self.assertEqual(payload["baseline_fingerprint"], ABSENT)
            self.assertEqual(payload["governed_file"], "shadow_authorization")
            alerts = list((root / "logs/incidents/alerts").glob("*.alert.json"))
            self.assertEqual(len(alerts), 1)

    def test_preexisting_record_without_baseline_is_not_trusted(self):
        # An authorization already on disk the first time the watch runs is
        # exactly what an unnoticed forgery looks like — never self-baseline it.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_authorization(root)
            result = check_authorization_record(NOW, project_root=root)
            self.assertEqual(result["status"], "CHANGED")

    def test_acknowledged_record_stops_alerting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            check_authorization_record(NOW, project_root=root)
            _write_authorization(root)
            check_authorization_record(NOW, project_root=root)

            acknowledge_authorization(project_root=root, acknowledged_by="owner test")
            result = check_authorization_record(NOW, project_root=root)

            self.assertEqual(result["status"], "UNCHANGED")
            self.assertFalse(result["changed"])

    def test_mutating_an_acknowledged_record_alerts_again(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_authorization(root)
            acknowledge_authorization(project_root=root, acknowledged_by="owner test")
            _write_authorization(root, approved_at="2026-09-01T00:00:00+00:00")

            result = check_authorization_record(NOW, project_root=root)

            self.assertEqual(result["status"], "CHANGED")

    def test_removing_an_acknowledged_record_alerts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_authorization(root)
            acknowledge_authorization(project_root=root, acknowledged_by="owner test")
            path.unlink()

            result = check_authorization_record(NOW, project_root=root)

            self.assertEqual(result["status"], "CHANGED")
            self.assertEqual(result["observed"]["shadow_authorization"], ABSENT)

    def test_incident_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            check_authorization_record(NOW, project_root=root)
            _write_authorization(root)
            first = check_authorization_record(NOW, project_root=root)
            body = Path(str(first["incident_path"])).read_text(encoding="utf-8")
            check_authorization_record(NOW, project_root=root)
            self.assertEqual(Path(str(first["incident_path"])).read_text(encoding="utf-8"), body)

    def test_kill_switch_arm_marker_appearing_is_a_critical_incident(self):
        # state/trading_armed DISARMS the kill switch by its mere existence and
        # is reachable by the same arbitrary-python3 path as the auth record.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            check_authorization_record(NOW, project_root=root)
            marker = root / "state/trading_armed"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")

            result = check_authorization_record(NOW, project_root=root)

            self.assertEqual(result["status"], "CHANGED")
            self.assertIn("trading_arm_marker", result["changed_files"])
            incident = json.loads(Path(str(result["incident_path"])).read_text(encoding="utf-8"))
            self.assertEqual(incident["severity"], "CRITICAL")
            self.assertEqual(incident["governed_file"], "trading_arm_marker")

    def test_all_governed_paths_are_watched(self):
        from monitoring.authorization_watch import GOVERNED_PATHS
        self.assertEqual(
            set(GOVERNED_PATHS.values()),
            {"state/shadow_authorization.json", "state/trading_armed", "state/automation_halt.json"},
        )

    def test_fingerprint_of_missing_file_is_absent(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(fingerprint(Path(tmp) / "nope.json"), ABSENT)


class OwnerOnlyAuthorizationPathTests(unittest.TestCase):
    def test_non_interactive_authorize_is_refused(self):
        # The unattended agent has no controlling terminal: the sanctioned CLI
        # path must refuse rather than mint an authorization.
        completed = subprocess.run(
            [sys.executable, "main.py", "shadow-authorize",
             "config/shadow_p0_qualification.example.json", "--owner-approved"],
            cwd=ROOT, capture_output=True, text=True, input="", timeout=60,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("OWNER_APPROVAL_REQUIRES_INTERACTIVE_TTY", completed.stdout)

    def test_watchdog_tick_reports_authorization_watch_status(self):
        source = (ROOT / "scripts/watchdog_tick.py").read_text(encoding="utf-8")
        self.assertIn("check_authorization_record", source)
        self.assertIn('"authorization_watch"', source)
        # A changed record must make the tick exit non-zero, like an incident.
        self.assertIn('authorization.get("changed")', source)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Retire past-day expectations and resolve their incidents — the sanctioned way.

Problem: expectation files from failed past days stay status=EXPECTED forever,
so every watchdog tick rescans them and the incident count drowns out TODAY's
signal. The sanctioned lifecycle (no TTL, no deletion, ever) is:

  1. Past-day expectations still EXPECTED  ->  status RETIRED_INCIDENT_RETAINED
     (scan_expected_runs skips retired entries; the file itself is kept).
  2. Their unresolved incidents get an explicit ``resolution`` object recorded
     by the owner, with the documented root cause.
  3. cleanup_expired_incidents.py (archive-only) then moves the RESOLVED
     incidents into logs/incidents/archive/ — moved, never deleted.

Only entries scheduled BEFORE the given cutoff date are touched; today's
expectations and incidents are never modified. Corrupt files are left alone
(fail closed).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import SESSION_TIMEZONE


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def retire_expectations(expected_dir: Path, cutoff: date, dry_run: bool) -> int:
    retired = 0
    if not expected_dir.is_dir():
        return retired
    for path in sorted(expected_dir.glob("*.expected.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # fail closed: leave corrupt files for the watchdog to flag
        if not isinstance(payload, dict) or payload.get("status") != "EXPECTED":
            continue
        try:
            scheduled = datetime.fromisoformat(str(payload["scheduled_for"]))
        except (KeyError, TypeError, ValueError):
            continue
        if scheduled.astimezone(SESSION_TIMEZONE).date() >= cutoff:
            continue
        payload["status"] = "RETIRED_INCIDENT_RETAINED"
        payload["retired_at"] = datetime.now(timezone.utc).isoformat()
        payload["retire_reason"] = "PAST_DAY_SLOT_NEVER_RECOVERABLE_NO_BACKFILL"
        if not dry_run:
            _atomic_json(path, payload)
        retired += 1
        print(f"  retired  {path.name}")
    return retired


def resolve_incidents(
    incident_dir: Path,
    cutoff: date,
    root_cause: str,
    resolved_by: str,
    dry_run: bool,
) -> int:
    resolved = 0
    if not incident_dir.is_dir():
        return resolved
    for path in sorted(incident_dir.glob("*.scheduler-incident.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # fail closed
        if not isinstance(payload, dict):
            continue
        resolution = payload.get("resolution")
        if isinstance(resolution, dict) and str(resolution.get("status") or "").strip():
            continue  # already resolved
        detected = str(payload.get("detected_at") or "")
        try:
            detected_date = datetime.fromisoformat(detected).astimezone(SESSION_TIMEZONE).date()
        except ValueError:
            continue
        # An incident belongs to the day of the run it flags, not the day the
        # batch reconcile happened to record it; prefer the run_id date stamp.
        run_id = str(payload.get("run_id") or "")
        run_date = None
        for token in run_id.split("-"):
            if len(token) == 8 and token.isdigit():
                try:
                    run_date = date(int(token[:4]), int(token[4:6]), int(token[6:8]))
                except ValueError:
                    run_date = None
                break
        incident_day = run_date or detected_date
        if incident_day >= cutoff:
            continue
        payload["resolution"] = {
            "status": "RESOLVED_ROOT_CAUSE_DOCUMENTED",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "resolved_by": resolved_by,
            "root_cause": root_cause,
        }
        if not dry_run:
            _atomic_json(path, payload)
        resolved += 1
        print(f"  resolved {path.name}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        default=None,
        help="Cutoff date YYYY-MM-DD (default: today PT). Only entries strictly before it are touched.",
    )
    parser.add_argument("--root-cause", required=True, help="Documented root cause recorded in each resolution")
    parser.add_argument("--resolved-by", required=True, help="Who is recording these resolutions")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cutoff = date.fromisoformat(args.before) if args.before else datetime.now(SESSION_TIMEZONE).date()

    print(f"Retiring expectations and resolving incidents for days before {cutoff} (dry-run={args.dry_run})")
    retired = retire_expectations(ROOT / "logs/scheduler/expected", cutoff, args.dry_run)
    resolved = resolve_incidents(
        ROOT / "logs/incidents", cutoff, args.root_cause, args.resolved_by, args.dry_run,
    )
    print(f"retired_expectations={retired} resolved_incidents={resolved}")
    print("Next: python3 scripts/cleanup_expired_incidents.py  (archives the now-resolved incidents; never deletes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

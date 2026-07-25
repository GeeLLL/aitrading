#!/usr/bin/env python3
"""Archive resolved scheduler incidents (never delete unresolved ones).

Safety posture: a scheduler incident is durable evidence. It is *never* deleted
by this script and *never* expires on a timer — an incident stops mattering only
when the owner records an explicit ``resolution`` object with a non-empty status
(see ``monitoring.scheduler_watchdog.unresolved_incident_ids``). Once explicitly
resolved, an incident is moved (not deleted) into an ``archive/`` subdirectory so
the active directory stays readable while the full record is preserved.

Unresolved, still-blocking, or unreadable/corrupt incident files are left exactly
where they are. Corrupt files in particular are treated as *blocking* by the
watchdog (fail closed), so deleting them would silently unblock the collector —
the opposite of what we want.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _is_resolved(path: Path) -> bool:
    """True only when the incident carries an explicit non-empty resolution status."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Unreadable/corrupt -> fail closed: treat as unresolved, leave in place.
        return False
    if not isinstance(payload, dict):
        return False
    resolution = payload.get("resolution")
    return isinstance(resolution, dict) and bool(str(resolution.get("status") or "").strip())


def archive_resolved_incidents(
    incident_dir: str | Path = "logs/incidents",
    dry_run: bool = False,
) -> int:
    """Move explicitly-resolved incident files into ``<incident_dir>/archive/``.

    Returns the number of incidents archived. Never deletes any file; never
    touches unresolved, blocking, or unreadable incidents.
    """
    directory = Path(incident_dir)
    if not directory.exists():
        return 0

    archive_dir = directory / "archive"
    archived = 0
    kept = 0

    for path in sorted(directory.glob("*.scheduler-incident.json")):
        if _is_resolved(path):
            destination = archive_dir / path.name
            if not dry_run:
                archive_dir.mkdir(parents=True, exist_ok=True)
                path.replace(destination)
            archived += 1
            print(f"  📦 {path.name} -> archive/ (resolved)")
        else:
            kept += 1
            print(f"  📌 {path.name} (unresolved — left in place, still blocking)")

    print(f"\n  archived={archived}  kept_blocking={kept}")
    return archived


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Archive explicitly-resolved scheduler incidents (never deletes)."
    )
    parser.add_argument(
        "--dir",
        default="logs/incidents",
        help="Incident directory (default: logs/incidents)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only; move nothing.",
    )

    args = parser.parse_args()

    print("════════════════════════════════════════════════════════════════")
    print(f"🧹 Archive resolved incidents (dry-run: {args.dry_run})")
    print("   Unresolved / corrupt incidents are NEVER deleted or expired.")
    print("════════════════════════════════════════════════════════════════")
    print()

    count = archive_resolved_incidents(incident_dir=args.dir, dry_run=args.dry_run)

    print()
    print("════════════════════════════════════════════════════════════════")
    if args.dry_run:
        print(f"✅ [DRY RUN] would archive {count} resolved incident(s)")
    else:
        print(f"✅ archived {count} resolved incident(s)")
    print("════════════════════════════════════════════════════════════════")

    sys.exit(0)

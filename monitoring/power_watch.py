"""Alert when the Mac is not on AC power during the collection window.

The 05:45 preflight checks AC power once. A drop to battery *during* the
session went unnoticed on 2026-07-28 — and a battery-powered Mac is at risk of
sleeping (or of launchd deferring timers) mid-window, which silently costs
slots. This is a read-only observation plus an idempotent daily incident.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, time as time_of_day, timezone
from pathlib import Path

from monitoring.daily_schedule import SESSION_TIMEZONE
from monitoring.market_calendar import is_market_open

# The window that must stay powered: from the preflight through the close slot.
WINDOW_START = time_of_day(5, 40)
WINDOW_END = time_of_day(13, 20)


def on_ac_power() -> bool | None:
    """True on AC, False on battery, None when pmset cannot be read."""
    try:
        result = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    head = result.stdout.splitlines()[0]
    if "AC Power" in head:
        return True
    if "Battery Power" in head:
        return False
    return None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_power(
    now: datetime,
    *,
    project_root: Path,
    incident_dir: Path | None = None,
    ac_power: bool | None = None,
) -> dict[str, object]:
    """Flag battery operation inside the collection window on a market day."""
    local = now.astimezone(SESSION_TIMEZONE)
    if not is_market_open(local.date()):
        return {"status": "OUTSIDE_MARKET_DAY", "on_ac": None}
    if not (WINDOW_START <= local.time() <= WINDOW_END):
        return {"status": "OUTSIDE_WINDOW", "on_ac": None}

    powered = on_ac_power() if ac_power is None else ac_power
    if powered is True:
        return {"status": "ON_AC", "on_ac": True}

    incidents = incident_dir if incident_dir is not None else project_root / "logs/incidents"
    reason = "ON_BATTERY" if powered is False else "POWER_STATE_UNKNOWN"
    incident_id = f"power-{reason.lower()}-{local.date().isoformat()}"
    incident_path = incidents / f"{incident_id}.scheduler-incident.json"
    if not incident_path.exists():
        _atomic_json(incident_path, {
            "schema_version": 1,
            "incident_type": "COLLECTION_WINDOW_POWER_RISK",
            "run_id": incident_id,
            "detected_at": now.astimezone(timezone.utc).isoformat(),
            "severity": "WARNING",
            "requires_owner_review": True,
            "reason": reason,
            "note": (
                "The Mac is not on AC power during the collection window. "
                "Sleep or deferred launchd timers can silently cost slots; "
                "plug in."
            ),
        })
        _atomic_json(incidents / "alerts" / f"{incident_id}.alert.json", {
            "schema_version": 1,
            "run_id": incident_id,
            "title": "采集窗口内未接电源",
            "message": "Mac 正在使用电池，可能导致槽位丢失；请接上电源。",
            "incident_path": str(incident_path),
        })
    return {"status": reason, "on_ac": powered, "incident_path": str(incident_path)}

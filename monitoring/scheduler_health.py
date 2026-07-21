from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class SchedulerHealth:
    healthy: bool
    reason: str
    scheduled_for: datetime
    acknowledged_at: datetime | None
    delay_seconds: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "reason": self.reason,
            "scheduled_for": self.scheduled_for.isoformat(),
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
            "delay_seconds": self.delay_seconds,
        }


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def write_start_ack(
    *,
    run_id: str,
    scheduled_for: datetime,
    acknowledged_at: datetime,
    directory: str | Path = "logs/scheduler",
) -> Path:
    """Atomically record proof that a scheduled process actually started."""

    if not run_id or not all(character.isalnum() or character in "-_" for character in run_id):
        raise ValueError("run_id must contain only letters, numbers, hyphens, and underscores")
    _aware(scheduled_for, "scheduled_for")
    _aware(acknowledged_at, "acknowledged_at")
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{run_id}.start.json"
    temporary = target.with_suffix(".json.tmp")
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "scheduled_for": scheduled_for.astimezone(timezone.utc).isoformat(),
        "acknowledged_at": acknowledged_at.astimezone(timezone.utc).isoformat(),
        "status": "STARTED",
    }
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def evaluate_start_ack(
    *,
    path: str | Path,
    scheduled_for: datetime,
    checked_at: datetime,
    grace_seconds: int = 120,
    expected_run_id: str | None = None,
) -> SchedulerHealth:
    """Fail closed when a scheduled-start acknowledgement is absent or invalid."""

    _aware(scheduled_for, "scheduled_for")
    _aware(checked_at, "checked_at")
    if grace_seconds < 0:
        raise ValueError("grace_seconds cannot be negative")
    ack_path = Path(path)
    if not ack_path.is_file():
        overdue = checked_at > scheduled_for + timedelta(seconds=grace_seconds)
        return SchedulerHealth(
            healthy=False,
            reason="SCHEDULED_RUN_MISSED" if overdue else "START_ACK_PENDING",
            scheduled_for=scheduled_for,
            acknowledged_at=None,
            delay_seconds=None,
        )
    try:
        payload = json.loads(ack_path.read_text(encoding="utf-8"))
        acknowledged_at = datetime.fromisoformat(str(payload["acknowledged_at"]))
        recorded_scheduled_for = datetime.fromisoformat(str(payload["scheduled_for"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return SchedulerHealth(False, "START_ACK_INVALID", scheduled_for, None, None)
    if acknowledged_at.tzinfo is None or recorded_scheduled_for.tzinfo is None:
        return SchedulerHealth(False, "START_ACK_INVALID", scheduled_for, None, None)
    if payload.get("schema_version") != 1 or payload.get("status") != "STARTED":
        return SchedulerHealth(False, "START_ACK_INVALID", scheduled_for, acknowledged_at, None)
    if expected_run_id is not None and payload.get("run_id") != expected_run_id:
        return SchedulerHealth(False, "START_ACK_RUN_ID_MISMATCH", scheduled_for, acknowledged_at, None)
    if recorded_scheduled_for.astimezone(timezone.utc) != scheduled_for.astimezone(timezone.utc):
        return SchedulerHealth(False, "START_ACK_SCHEDULE_MISMATCH", scheduled_for, acknowledged_at, None)
    delay = (acknowledged_at - scheduled_for).total_seconds()
    if delay > grace_seconds:
        return SchedulerHealth(False, "START_ACK_LATE", scheduled_for, acknowledged_at, delay)
    return SchedulerHealth(True, "START_ACK_VALID", scheduled_for, acknowledged_at, delay)

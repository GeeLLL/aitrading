from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from monitoring.scheduler_health import SchedulerHealth, evaluate_start_ack


@dataclass(frozen=True)
class WatchdogResult:
    health: SchedulerHealth
    incident_path: Path | None


def catch_up_policy(run_id: str) -> str:
    """Return the only permitted recovery action for a missed scheduled run."""

    if run_id.startswith("pilot-close-canary-"):
        return "LOCAL_LOG_SUMMARY_ONLY_MARK_INCOMPLETE"
    return "DO_NOT_BACKFILL_MARKET_SAMPLE"


def register_expected_run(
    *,
    run_id: str,
    scheduled_for: datetime,
    directory: str | Path = "logs/scheduler/expected",
) -> Path:
    """Register an expectation before the scheduled worker is due."""

    if not run_id or not all(character.isalnum() or character in "-_" for character in run_id):
        raise ValueError("run_id must contain only letters, numbers, hyphens, and underscores")
    if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
        raise ValueError("scheduled_for must be timezone-aware")
    path = Path(directory) / f"{run_id}.expected.json"
    _atomic_json(path, {
        "schema_version": 1,
        "run_id": run_id,
        "scheduled_for": scheduled_for.astimezone(timezone.utc).isoformat(),
        "status": "EXPECTED",
    })
    return path


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_expected_run(
    *,
    run_id: str,
    scheduled_for: datetime,
    checked_at: datetime,
    ack_directory: str | Path = "logs/scheduler",
    incident_directory: str | Path = "logs/incidents",
    grace_seconds: int = 120,
) -> WatchdogResult:
    """Audit one expected run and durably record actionable scheduler failures.

    START_ACK_PENDING is not an incident until the grace window expires. Incident
    writes are idempotent, so an independent watchdog may safely poll repeatedly.
    """

    ack_path = Path(ack_directory) / f"{run_id}.start.json"
    health = evaluate_start_ack(
        path=ack_path,
        scheduled_for=scheduled_for,
        checked_at=checked_at,
        grace_seconds=grace_seconds,
        expected_run_id=run_id,
    )
    if health.healthy or health.reason == "START_ACK_PENDING":
        return WatchdogResult(health=health, incident_path=None)

    incident_path = Path(incident_directory) / f"{run_id}.scheduler-incident.json"
    is_new_incident = not incident_path.exists()
    if is_new_incident:
        _atomic_json(
            incident_path,
            {
                "schema_version": 1,
                "incident_type": "SCHEDULER_START_FAILURE",
                "run_id": run_id,
                "detected_at": checked_at.astimezone(timezone.utc).isoformat(),
                "ack_path": str(ack_path),
                "health": health.to_dict(),
                "severity": "CRITICAL",
                "new_entries_blocked": True,
                "requires_owner_review": True,
                "catch_up_policy": catch_up_policy(run_id),
            },
        )
        alert_path = incident_path.parent / "alerts" / f"{run_id}.alert.json"
        _atomic_json(alert_path, {
            "schema_version": 1,
            "run_id": run_id,
            "title": "Robinhood 实验调度事故",
            "message": f"{run_id}: {health.reason}; 新开仓保持关闭。",
            "incident_path": str(incident_path),
        })
    return WatchdogResult(health=health, incident_path=incident_path)


def scan_expected_runs(
    *,
    checked_at: datetime,
    expectation_directory: str | Path = "logs/scheduler/expected",
    ack_directory: str | Path = "logs/scheduler",
    incident_directory: str | Path = "logs/incidents",
    grace_seconds: int = 120,
) -> tuple[WatchdogResult, ...]:
    """Scan pre-registered expectations; malformed manifests fail closed."""

    expectation_dir = Path(expectation_directory)
    if not expectation_dir.exists():
        return ()
    results: list[WatchdogResult] = []
    for path in sorted(expectation_dir.glob("*.expected.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1:
                raise ValueError("invalid expectation envelope")
            if payload.get("status") == "RETIRED_INCIDENT_RETAINED":
                continue
            if payload.get("status") != "EXPECTED":
                raise ValueError("invalid expectation envelope")
            run_id = str(payload["run_id"])
            scheduled_for = datetime.fromisoformat(str(payload["scheduled_for"]))
            if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
                raise ValueError("naive expectation timestamp")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            synthetic_id = path.name.removesuffix(".expected.json")
            incident_path = Path(incident_directory) / f"{synthetic_id}.scheduler-incident.json"
            if not incident_path.exists():
                _atomic_json(incident_path, {
                    "schema_version": 1,
                    "incident_type": "SCHEDULER_EXPECTATION_INVALID",
                    "run_id": synthetic_id,
                    "detected_at": checked_at.astimezone(timezone.utc).isoformat(),
                    "expectation_path": str(path),
                    "severity": "CRITICAL",
                    "new_entries_blocked": True,
                    "requires_owner_review": True,
                })
            results.append(WatchdogResult(
                health=SchedulerHealth(
                    healthy=False,
                    reason="SCHEDULER_EXPECTATION_INVALID",
                    scheduled_for=checked_at,
                    acknowledged_at=None,
                    delay_seconds=None,
                ),
                incident_path=incident_path,
            ))
            continue
        results.append(check_expected_run(
            run_id=run_id,
            scheduled_for=scheduled_for,
            checked_at=checked_at,
            ack_directory=ack_directory,
            incident_directory=incident_directory,
            grace_seconds=grace_seconds,
        ))
    return tuple(results)

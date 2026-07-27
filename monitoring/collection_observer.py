"""Single, pure, read-only observer for the shadow collector.

This is the one place that answers "how is collection doing right now?" It
replaces three overlapping daemons (`robust_sampling_coordinator`,
`enhanced_monitor`, `active_shadow_monitor`) whose logic had drifted apart and,
worse, whose coordinator *backfilled* missed market samples — a policy
violation. This module is deliberately inert: it has no subprocess calls, no
retries, no backfill, and no sleep. It reads the durable artifacts the worker
and watchdog already write and reports a status; acting on that status (alerting)
is the caller's job.

All market-hours logic comes from the corrected `market_calendar`, so weekends,
holidays, and early closes are handled in exactly one place.

It also exposes `ensure_day_registered`, which idempotently registers the current
market day's expectations. Wiring this into the frequently-running watchdog tick
closes the "the Mac slept through every worker slot" gap: on wake the watchdog
back-registers the day and immediately flags the slots that never ran, so a
fully-slept day surfaces as visible incidents instead of vanishing silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from monitoring.daily_schedule import SESSION_TIMEZONE, expected_runs_for_date
from monitoring.market_calendar import is_market_open
from monitoring.scheduler_watchdog import register_expected_run, unresolved_incident_ids

DEFAULT_EXPECTATION_DIR = "logs/scheduler/expected"
DEFAULT_ACK_DIR = "logs/scheduler"
DEFAULT_INCIDENT_DIR = "logs/incidents"
DEFAULT_WORKER_DIR = "logs/launchd_worker"

# A slot is not yet "missed" until this long past its scheduled time (mirrors the
# watchdog's start-ack grace window).
GRACE_SECONDS = 120

# Worker-summary statuses that mean the slot started (wrote an ack) but its
# collection did NOT succeed. This is how a systemic outage that still "starts"
# every slot — a dead MCP OAuth, a missing/expired Claude CLI, a network drop —
# shows up as DEGRADED instead of a day that looks fully healthy because 17 acks
# exist. "COMPLETED" is the ONLY non-failure; an unknown status is treated as a
# failure (fail toward visible). OVERLAP_SKIPPED is deliberately a failure: it
# means the slot's sample was lost because an earlier worker still held the
# lock — a hung-worker day previously looked fully healthy through 16 of these.
_SUCCESS_STATUSES = frozenset({"COMPLETED"})


@dataclass(frozen=True)
class SlotObservation:
    run_id: str
    scheduled_for: datetime
    state: str  # PENDING | RAN | RAN_FAILED | MISSED
    summary_status: str | None = None


@dataclass(frozen=True)
class CollectionStatus:
    market_open: bool
    as_of: datetime
    slots: tuple[SlotObservation, ...] = ()
    unresolved_incidents: tuple[str, ...] = ()

    @property
    def missed(self) -> tuple[SlotObservation, ...]:
        return tuple(slot for slot in self.slots if slot.state == "MISSED")

    @property
    def ran(self) -> tuple[SlotObservation, ...]:
        """Slots that started and did not report a collection failure."""
        return tuple(slot for slot in self.slots if slot.state == "RAN")

    @property
    def failed(self) -> tuple[SlotObservation, ...]:
        """Slots that started (wrote an ack) but whose collection failed."""
        return tuple(slot for slot in self.slots if slot.state == "RAN_FAILED")

    @property
    def pending(self) -> tuple[SlotObservation, ...]:
        return tuple(slot for slot in self.slots if slot.state == "PENDING")

    @property
    def healthy(self) -> bool:
        return not self.missed and not self.failed and not self.unresolved_incidents

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "market_open": self.market_open,
            "overall": "HEALTHY" if self.healthy else "DEGRADED",
            "counts": {
                "expected": len(self.slots),
                "ran": len(self.ran),
                "failed": len(self.failed),
                "missed": len(self.missed),
                "pending": len(self.pending),
                "unresolved_incidents": len(self.unresolved_incidents),
            },
            "missed": [
                {"run_id": slot.run_id, "scheduled_for": slot.scheduled_for.isoformat()}
                for slot in self.missed
            ],
            "failed": [
                {"run_id": slot.run_id, "status": slot.summary_status}
                for slot in self.failed
            ],
            "unresolved_incidents": list(self.unresolved_incidents),
            "policy": "OBSERVE_ONLY_NEVER_BACKFILL",
        }


def _ack_exists(run_id: str, ack_directory: Path) -> bool:
    return (ack_directory / f"{run_id}.start.json").is_file()


def _summary_status(run_id: str, scheduled_for: datetime, worker_directory: Path) -> str | None:
    local_date = scheduled_for.astimezone(SESSION_TIMEZONE).date().isoformat()
    summary = worker_directory / local_date / f"{run_id}.json"
    if not summary.is_file():
        return None
    try:
        payload = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "UNREADABLE_SUMMARY"
    return str(payload.get("status") or "UNKNOWN") if isinstance(payload, dict) else "UNKNOWN"


def observe_collection(
    now: datetime,
    *,
    project_root: str | Path = ".",
    expectation_directory: str | Path | None = None,
    ack_directory: str | Path | None = None,
    incident_directory: str | Path | None = None,
    worker_directory: str | Path | None = None,
) -> CollectionStatus:
    """Compute a read-only snapshot of today's collection health. Pure; no side effects."""
    root = Path(project_root)
    ack_dir = Path(ack_directory) if ack_directory is not None else root / DEFAULT_ACK_DIR
    incident_dir = Path(incident_directory) if incident_directory is not None else root / DEFAULT_INCIDENT_DIR
    worker_dir = Path(worker_directory) if worker_directory is not None else root / DEFAULT_WORKER_DIR

    # The observation day is the session (PT) date the slots are defined in and
    # the worker registers under — keep it consistent so run_ids line up.
    today = now.astimezone(SESSION_TIMEZONE).date()
    incidents = unresolved_incident_ids(incident_dir)

    if not is_market_open(today):
        return CollectionStatus(market_open=False, as_of=now, slots=(), unresolved_incidents=incidents)

    slots: list[SlotObservation] = []
    for run_id, scheduled_for in expected_runs_for_date(today):
        ran = _ack_exists(run_id, ack_dir)
        summary_status = _summary_status(run_id, scheduled_for, worker_dir) if ran else None
        if ran:
            # Started, but did the collection actually succeed? A finished summary
            # with a non-success status (dead OAuth, missing CLI, timeout, ...) is
            # a visible failure, not a healthy run. A still-running slot has no
            # summary yet (None) and counts as RAN until its summary lands.
            if summary_status is not None and summary_status not in _SUCCESS_STATUSES:
                state = "RAN_FAILED"
            else:
                state = "RAN"
        elif now > scheduled_for + timedelta(seconds=GRACE_SECONDS):
            # Past the slot's grace window with no start-ack -> a real miss.
            state = "MISSED"
        else:
            # Future slot, or within the grace window: not yet a miss.
            state = "PENDING"
        slots.append(SlotObservation(
            run_id=run_id,
            scheduled_for=scheduled_for,
            state=state,
            summary_status=summary_status,
        ))
    return CollectionStatus(
        market_open=True,
        as_of=now,
        slots=tuple(slots),
        unresolved_incidents=incidents,
    )


def ensure_day_registered(
    now: datetime,
    *,
    project_root: str | Path = ".",
    expectation_directory: str | Path | None = None,
) -> int:
    """Idempotently register the current market day's expectations.

    No-ops on non-market days. Registering the whole day means a slot that never
    fires (e.g. because the Mac slept through it) still has an expectation on
    record, so the watchdog flags it as a miss instead of it vanishing. Safe to
    call from any frequently-running job (the watchdog tick in particular).
    Returns the number of expectations registered (0 on a closed day).
    """
    root = Path(project_root)
    directory = Path(expectation_directory) if expectation_directory is not None else root / DEFAULT_EXPECTATION_DIR
    today = now.astimezone(SESSION_TIMEZONE).date()
    if not is_market_open(today):
        return 0
    count = 0
    for run_id, scheduled_for in expected_runs_for_date(today):
        register_expected_run(run_id=run_id, scheduled_for=scheduled_for, directory=directory)
        count += 1
    return count


def render_report(status: CollectionStatus) -> str:
    """A compact human-readable one-screen report for the observe-only daemons."""
    if not status.market_open:
        head = f"[{status.as_of:%Y-%m-%d %H:%M}] market CLOSED"
        if status.unresolved_incidents:
            head += f" — {len(status.unresolved_incidents)} unresolved incident(s)"
        return head
    counts = status.to_dict()["counts"]
    lines = [
        f"[{status.as_of:%Y-%m-%d %H:%M}] {'HEALTHY' if status.healthy else 'DEGRADED'} "
        f"ran={counts['ran']}/{counts['expected']} "
        f"failed={counts['failed']} missed={counts['missed']} pending={counts['pending']} "
        f"incidents={counts['unresolved_incidents']}",
    ]
    for slot in status.failed:
        lines.append(f"  FAILED {slot.run_id} ({slot.summary_status})")
    for slot in status.missed:
        lines.append(f"  MISSED {slot.run_id} (scheduled {slot.scheduled_for:%H:%M})")
    return "\n".join(lines)

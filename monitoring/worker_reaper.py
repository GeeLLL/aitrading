"""Detect and reap hung launchd collector workers.

A worker that hangs after writing its start ack holds the exclusive flock and
silently starves every later slot (each later fire records OVERLAP_SKIPPED).
The worker now writes a PID record (``logs/scheduler/<run_id>.pid``) for its
whole lifetime; the watchdog calls :func:`reap_overdue_workers` every tick and
kills any worker that is past its deadline with no summary written, filing a
CRITICAL incident + alert so a human hears about it.

Killing the read-only collector is always safe: it holds no positions and
mutates nothing but its own log files. Reaping frees the flock so the NEXT
slot collects normally — the hung slot itself is lost and is NEVER backfilled.

The classification logic is pure (:func:`classify_worker`) so it can be unit
tested without processes; only the thin wrappers touch ps/kill.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# Seconds from worker start until it is declared hung. Canary: 300s collector
# cap + retry budget + dashboard rebuild << 780. Pilot kinds: 720s agent cap +
# fast-failure retry (240 + 480) + overhead < 1020. Both stay under the 1200s
# slot spacing, so a reap always lands before the next slot fires.
DEADLINE_SECONDS = {"CANARY": 780}
DEFAULT_DEADLINE_SECONDS = 1020

# classify_worker verdicts
RUNNING_OK = "RUNNING_OK"                # within deadline — leave alone
FINISHED_STALE = "FINISHED_STALE"        # summary exists — stale record, just unlink
OVERDUE_KILL = "OVERDUE_KILL"            # overdue, our process alive — kill it
DIED_NO_SUMMARY = "DIED_NO_SUMMARY"      # overdue, process gone, no summary — crashed hard
PID_REUSED = "PID_REUSED"                # overdue, pid now belongs to something else
INVALID_RECORD = "INVALID_RECORD"        # unreadable/malformed pid record


def classify_worker(
    record: dict,
    *,
    now: datetime,
    summary_exists: bool,
    process_alive: bool,
    cmdline: str,
) -> str:
    """Pure decision: what should happen to this worker PID record?

    The overdue anchor is the slot's SCHEDULED time when recorded (falling
    back to the worker's start time for older records): a fire admitted up to
    180s late must still be reaped before the next slot fires, so the deadline
    cannot float with the actual start.
    """
    try:
        started_at = datetime.fromisoformat(str(record["started_at"]))
        kind = str(record.get("kind") or "")
        int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return INVALID_RECORD
    if started_at.tzinfo is None:
        return INVALID_RECORD
    anchor = started_at
    scheduled_raw = record.get("scheduled_for")
    if scheduled_raw:
        try:
            scheduled = datetime.fromisoformat(str(scheduled_raw))
            if scheduled.tzinfo is not None:
                anchor = min(anchor, scheduled)
        except ValueError:
            pass
    if summary_exists:
        return FINISHED_STALE
    deadline = DEADLINE_SECONDS.get(kind, DEFAULT_DEADLINE_SECONDS)
    if (now - anchor).total_seconds() <= deadline:
        return RUNNING_OK
    if not process_alive:
        return DIED_NO_SUMMARY
    if "launchd_shadow_worker" not in cmdline:
        return PID_REUSED
    return OVERDUE_KILL


def _process_cmdline(pid: int) -> tuple[bool, str]:
    """Return (alive, command line) for a pid via ps (macOS has no /proc)."""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return False, ""
    return True, result.stdout.strip()


def _kill_process(pid: int) -> None:
    """SIGTERM, short grace, then SIGKILL if still alive."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_child_group(record: dict) -> bool:
    """Kill the worker's recorded Claude CLI process group, if it is still the
    CLI. The worker starts the CLI in its own session (pgid == child_pid); if
    only the parent dies, this orphan has NO remaining timeout and would keep
    collecting past the slot — it must never survive a reap. Guarded by a
    cmdline check so a recycled pid is never killed."""
    child_pid = record.get("child_pid")
    if not isinstance(child_pid, int):
        return False
    alive, cmdline = _process_cmdline(child_pid)
    if not alive or "claude" not in cmdline:
        return False
    try:
        os.killpg(child_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    for _ in range(6):
        time.sleep(0.5)
        try:
            os.killpg(child_pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
    try:
        os.killpg(child_pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return True


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _file_incident(
    incident_dir: Path,
    run_id: str,
    incident_type: str,
    detail: dict[str, object],
    detected_at: datetime,
) -> None:
    incident_path = incident_dir / f"{run_id}-hung.scheduler-incident.json"
    if incident_path.exists():
        return
    _atomic_json(incident_path, {
        "schema_version": 1,
        "incident_type": incident_type,
        "run_id": run_id,
        "detected_at": detected_at.astimezone(timezone.utc).isoformat(),
        "severity": "CRITICAL",
        "new_entries_blocked": True,
        "requires_owner_review": True,
        "catch_up_policy": "DO_NOT_BACKFILL_MARKET_SAMPLE",
        **detail,
    })
    _atomic_json(incident_dir / "alerts" / f"{run_id}-hung.alert.json", {
        "schema_version": 1,
        "run_id": run_id,
        "title": "Robinhood worker 挂起已处理",
        "message": f"{run_id}: {incident_type}; 该槽位样本丢失(不回补), 锁已释放。",
        "incident_path": str(incident_path),
    })


def reap_overdue_workers(
    now: datetime,
    *,
    project_root: Path,
    scheduler_dir: Path | None = None,
    incident_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Scan PID records; kill hung workers; file incidents. Returns actions taken."""
    sched = scheduler_dir if scheduler_dir is not None else project_root / "logs/scheduler"
    incidents = incident_dir if incident_dir is not None else project_root / "logs/incidents"
    actions: list[dict[str, str]] = []
    if not sched.is_dir():
        return actions
    for pid_path in sorted(sched.glob("*.pid")):
        try:
            record = json.loads(pid_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                record = {}
        except (OSError, json.JSONDecodeError):
            record = {}
        summary_exists = False
        summary_path = record.get("summary_path")
        if isinstance(summary_path, str) and summary_path:
            summary_exists = Path(summary_path).is_file()
        pid = record.get("pid")
        alive, cmdline = (False, "")
        if isinstance(pid, int):
            alive, cmdline = _process_cmdline(pid)
        verdict = classify_worker(
            record, now=now, summary_exists=summary_exists,
            process_alive=alive, cmdline=cmdline,
        )
        run_id = str(record.get("run_id") or pid_path.stem)
        if verdict == RUNNING_OK:
            continue
        if verdict == OVERDUE_KILL:
            child_killed = _kill_child_group(record)
            _kill_process(int(record["pid"]))
            _file_incident(incidents, run_id, "WORKER_HUNG_KILLED", {
                "pid": record["pid"],
                "started_at": record.get("started_at"),
                "kind": record.get("kind"),
                "child_group_killed": child_killed,
            }, now)
        elif verdict == DIED_NO_SUMMARY:
            # The parent crashed; its CLI child may live on as an orphan with
            # no timeout — reap the recorded group too.
            child_killed = _kill_child_group(record)
            _file_incident(incidents, run_id, "WORKER_DIED_NO_SUMMARY", {
                "pid": record.get("pid"),
                "started_at": record.get("started_at"),
                "kind": record.get("kind"),
                "child_group_killed": child_killed,
            }, now)
        elif verdict == PID_REUSED:
            _file_incident(incidents, run_id, "WORKER_PID_RECORD_STALE", {
                "pid": record.get("pid"),
                "observed_cmdline": cmdline[:200],
            }, now)
        # FINISHED_STALE / INVALID_RECORD / post-action: the record is spent.
        pid_path.unlink(missing_ok=True)
        actions.append({"run_id": run_id, "verdict": verdict})
    return actions

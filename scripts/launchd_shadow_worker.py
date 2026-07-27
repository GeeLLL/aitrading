from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from execution.official_mcp_collector import (
    OfficialCollectorError,
    claude_binary,
    collect_official_raw_snapshot,
    read_only_allowed_tools,
)
from execution.raw_data_vault import RawDataVault
from main import build_status
from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, run_id_for
from monitoring.kill_switch import AutomationHalt
from monitoring.scheduler_health import write_start_ack
from monitoring.scheduler_watchdog import unresolved_incident_ids


LOCAL = SESSION_TIMEZONE
LOCK_PATH = ROOT / "logs/scheduler/launchd-shadow-worker.lock"
SLOTS = DAILY_SLOTS

# In-slot retry budgets. Retrying INSIDE the already-acked slot is not backfill:
# the fire passed the 180s freshness guard and the retry never extends past the
# slot's own execution window (slots are >= 1200s apart; worst case below stays
# well under that). The budgets exist so a retry can never collide with the
# next slot.
CANARY_RETRY_ELAPSED_CAP_SECONDS = 420   # no 2nd canary attempt after this
PILOT_FAST_FAILURE_SECONDS = 240         # pilot retry only if attempt 1 died faster than this
PILOT_RETRY_TIMEOUT_SECONDS = 480        # and the retry itself gets a tighter cap
PILOT_TIMEOUT_SECONDS = 720

# The pilot agent needs read-only Robinhood MCP tools plus the ability to run
# the project's deterministic CLI and write its own logs inside the workspace.
# Everything else stays denied by Claude Code's print-mode default.
PILOT_ALLOWED_TOOLS = ",".join((
    read_only_allowed_tools(),
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "Bash(python3:*)",
    "Bash(/Library/Frameworks/Python.framework/Versions/3.13/bin/python3:*)",
))


def _log_root(now: datetime) -> Path:
    return ROOT / "logs/launchd_worker" / now.astimezone(LOCAL).date().isoformat()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _resolve_slot(now: datetime, slot_hhmm: str | None = None) -> tuple[datetime, str, str]:
    """Resolve the slot this fire belongs to, enforcing a 180s freshness guard.

    The freshness guard is the backfill firewall: a fire that lands more than
    180 seconds from any registered slot (e.g. launchd replaying a missed
    StartCalendarInterval hours after the Mac wakes) is REFUSED, so a stale
    market sample is never collected after the fact.

    ``slot_hhmm`` is an optional "HHMM" hint set by the self-arming wrapper to
    name the exact slot it fired for. It only disambiguates *which* slot; the
    same 180s freshness guard still applies, so the hint can never be used to
    backfill.
    """
    if slot_hhmm:
        try:
            hour, minute = int(slot_hhmm[:2]), int(slot_hhmm[2:])
            kind, symbol = SLOTS[(hour, minute)]
        except (ValueError, KeyError, IndexError):
            raise ValueError(f"UNKNOWN_SLOT_HHMM:{slot_hhmm}")
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if abs((now - scheduled).total_seconds()) > 180:
            raise ValueError("SLOT_FIRED_OUTSIDE_180_SECONDS")
        return scheduled, kind, symbol

    candidates = []
    for (hour, minute), (kind, symbol) in SLOTS.items():
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append((abs((now - scheduled).total_seconds()), scheduled, kind, symbol))
    distance, scheduled, kind, symbol = min(candidates, key=lambda row: row[0])
    if distance > 180:
        raise ValueError("NO_REGISTERED_SLOT_WITHIN_180_SECONDS")
    return scheduled, kind, symbol


def _run_id(scheduled: datetime, kind: str) -> str:
    return run_id_for(kind, scheduled)


def _safety_ok(incident_directory: Path = ROOT / "logs/incidents") -> tuple[bool, dict[str, object]]:
    """Order-safety gate for the read-only collector.

    Read-only collection is gated ONLY on order-safety invariants and the
    explicit automation-halt kill switch. Scheduler incidents are a reliability
    signal, not a safety signal: re-collecting read-only market data is harmless,
    so a collection miss must never brick the next run. We still surface any
    unresolved incidents in ``status`` for visibility and alerting, but they do
    not gate the collector. (Order-safety fail-closed lives in the risk / order
    path, which never runs during read-only collection. There is no environment
    variable bypass — the invariants below are non-negotiable.)
    """
    status = build_status()
    halted = AutomationHalt(ROOT / "state/automation_halt.json").active()
    status["automation_halted"] = halted
    status["unresolved_scheduler_incidents"] = list(
        unresolved_incident_ids(incident_directory)
    )

    valid = (
        status["system_mode"] == "READ_ONLY"
        and status["live_trading_enabled"] is False
        and status["order_tools_enabled"] is False
        and status["kill_switch_engaged"] is True
        and not halted
    )
    return valid, status


def _rebuild_dashboard() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_shadow_dashboard.py")],
        cwd=ROOT,
        timeout=30,
        check=False,
    )


def _run_canary(run_id: str, symbol: str, ack_path: Path, log_root: Path) -> int:
    """Exercise launchd -> official read-only MCP -> immutable local evidence."""
    summary_path = log_root / f"{run_id}.json"
    started = datetime.now(timezone.utc)
    # Bounded in-slot retry: a transient CLI/stream failure gets one more
    # attempt while still comfortably inside this slot's execution window.
    receipt = None
    verified = None
    attempts = 0
    attempt_errors: list[str] = []
    for attempt in (1, 2):
        attempts = attempt
        try:
            # Read-only canary: degrade gracefully if one large tool overflows the
            # harness cap, so a single bad tool never zeroes the snapshot. Any partial
            # result is marked in the vault envelope and stays excluded from evidence.
            receipt = collect_official_raw_snapshot(symbol, project_root=ROOT, resilient=True)
            verified = RawDataVault.verify(receipt.path, receipt.content_sha256)
            break
        except (OfficialCollectorError, ValueError) as error:
            attempt_errors.append(f"attempt {attempt}: {type(error).__name__}: {error}")
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            if elapsed > CANARY_RETRY_ELAPSED_CAP_SECONDS:
                break
    result_status = "COMPLETED" if verified else "FAILED_CLOSED"
    failure_reason = None if verified else "; ".join(attempt_errors) or None
    ended = datetime.now(timezone.utc)
    _atomic_json(summary_path, {
        "schema_version": 1,
        "status": result_status,
        "run_id": run_id,
        "kind": "CANARY",
        "symbol": symbol,
        "ack_path": str(ack_path),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "attempts": attempts,
        "snapshot_path": str(verified.path) if verified else None,
        "snapshot_sha256": verified.content_sha256 if verified else None,
        "failure_reason": failure_reason,
        "read_only": True,
        "live_trading_enabled": False,
        "order_tools_enabled": False,
        "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
    })
    _rebuild_dashboard()
    return 0 if verified else 2


def _kill_process_group(pgid: int) -> None:
    """SIGTERM the group, brief grace, then SIGKILL. Never raises."""
    import signal
    import time
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(6):
        time.sleep(0.5)
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_agent_once(
    command: list[str],
    prompt: str,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
    attempt: int,
    pid_path: Path | None = None,
) -> tuple[int, bool]:
    """Run one Claude CLI attempt. Returns (return_code, timed_out).

    Output is APPENDED with an attempt banner so a retry can never destroy the
    evidence of the first attempt (the old code truncated stderr on timeout).

    The CLI runs in its OWN process group (start_new_session), and that group
    id is recorded in the worker's pid record: if this parent is killed (e.g.
    by the watchdog reaper), the child must never survive as an untimed orphan
    that keeps collecting past the slot — the reaper kills the recorded group.
    On our own timeout the whole group is killed here for the same reason.
    """
    banner = f"\n===== attempt {attempt} @ {datetime.now(timezone.utc).isoformat()} =====\n"
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open("a", encoding="utf-8") as stderr:
        stdout.write(banner)
        stderr.write(banner)
        stdout.flush()
        stderr.flush()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                cwd=ROOT,
                start_new_session=True,
            )
        except OSError as error:
            stderr.write(f"{type(error).__name__}: {error}\n")
            return 2, False
        if pid_path is not None and pid_path.is_file():
            try:
                record = json.loads(pid_path.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    record["child_pid"] = process.pid  # == pgid (new session)
                    _atomic_json(pid_path, record)
            except (OSError, json.JSONDecodeError):
                pass
        try:
            process.communicate(input=prompt, timeout=timeout_seconds)
            return process.returncode, False
        except subprocess.TimeoutExpired:
            stderr.write(f"TimeoutExpired after {timeout_seconds}s\n")
            _kill_process_group(process.pid)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            return 2, True
        finally:
            if process.poll() is None:
                _kill_process_group(process.pid)


def _execute_slot(
    *,
    run_id: str,
    kind: str,
    symbol: str,
    scheduled: datetime,
    now: datetime,
    ack_path: Path,
    log_root: Path,
    summary_path: Path,
    pid_path: Path | None = None,
) -> int:
    safe, safety = _safety_ok()
    if not safe:
        _atomic_json(summary_path, {
            "status": "SAFETY_GATE_FAILED",
            "run_id": run_id,
            "safety": safety,
        })
        return 2

    if kind == "CANARY":
        return _run_canary(run_id, symbol, ack_path, log_root)

    prompt = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8").format(
        run_id=run_id,
        kind=kind,
        scheduled_for=scheduled.isoformat(),
        symbol=symbol,
        log_root=str(log_root),
        trajectory_root=str(ROOT / "logs/quote_trajectories" / now.astimezone(LOCAL).date().isoformat()),
    )
    stdout_path = log_root / f"{run_id}.stdout.jsonl"
    stderr_path = log_root / f"{run_id}.stderr.log"
    try:
        command = [
            claude_binary(), "-p",
            "--output-format", "stream-json", "--verbose",
            "--allowedTools", PILOT_ALLOWED_TOOLS,
        ]
    except OfficialCollectorError as error:
        _atomic_json(summary_path, {
            "status": "CLAUDE_CLI_NOT_FOUND",
            "run_id": run_id,
            "reason": str(error),
            "ack_path": str(ack_path),
        })
        return 2

    started = datetime.now(timezone.utc)
    return_code, timed_out = _run_agent_once(
        command, prompt, stdout_path, stderr_path, PILOT_TIMEOUT_SECONDS, attempt=1,
        pid_path=pid_path,
    )
    attempts = 1
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if return_code != 0 and not timed_out and elapsed < PILOT_FAST_FAILURE_SECONDS:
        # Fast failure = CLI startup/auth/transport problem, not a long agent
        # run. One tighter-capped retry still fits inside this slot's window.
        attempts = 2
        return_code, timed_out = _run_agent_once(
            command, prompt, stdout_path, stderr_path, PILOT_RETRY_TIMEOUT_SECONDS, attempt=2,
            pid_path=pid_path,
        )

    if return_code == 0:
        # Exit code 0 alone is not completion: the agent must have written its
        # own terminal summary (the prompt names the exact path). This catches
        # "CLI exited clean but did nothing" — e.g. every MCP call errored.
        agent_summary = log_root / f"{run_id}.summary.json"
        if agent_summary.is_file():
            result_status = "COMPLETED"
        else:
            result_status = "COMPLETED_NO_AGENT_SUMMARY"
            return_code = 2
    elif timed_out:
        result_status = "AGENT_TIMEOUT_OR_START_FAILURE"
    else:
        result_status = "AGENT_FAILED"

    ended = datetime.now(timezone.utc)
    _atomic_json(summary_path, {
        "schema_version": 1,
        "status": result_status,
        "run_id": run_id,
        "kind": kind,
        "symbol": symbol,
        "scheduled_for": scheduled.astimezone(timezone.utc).isoformat(),
        "ack_path": str(ack_path),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "attempts": attempts,
        "agent_runtime": "CLAUDE_CODE_CLI",
        "agent_return_code": return_code,
        "read_only": True,
        "live_trading_enabled": False,
        "order_tools_enabled": False,
        "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
    })

    if kind == "CLOSE_SUMMARY":
        # Deterministic end-of-day report: runs regardless of how the agent
        # fared, so the day always ends with an auditable P&L/coverage record.
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/eod_report.py"),
                "--date", now.astimezone(LOCAL).date().isoformat(),
            ],
            cwd=ROOT,
            timeout=120,
            check=False,
        )

    _rebuild_dashboard()
    return 0 if return_code == 0 else 2


def main() -> int:
    now = datetime.now(LOCAL)
    log_root = _log_root(now)
    log_root.mkdir(parents=True, exist_ok=True)
    if os.environ.get("ROBINHOOD_SHADOW_CANARY") == "1":
        scheduled = now.replace(second=0, microsecond=0)
        kind, symbol = "CANARY", "SPY"
    else:
        try:
            scheduled, kind, symbol = _resolve_slot(now, os.environ.get("ROBINHOOD_SLOT_HHMM"))
        except ValueError as error:
            _atomic_json(log_root / f"unscheduled-{now:%H%M%S}.json", {
                "status": "REFUSED",
                "reason": str(error),
                "observed_at": now.astimezone(timezone.utc).isoformat(),
            })
            return 2
    run_id = _run_id(scheduled, kind)
    summary_path = log_root / f"{run_id}.json"
    try:
        ack_path = write_start_ack(
            run_id=run_id,
            scheduled_for=scheduled,
            acknowledged_at=now,
        )
    except ValueError as error:
        _atomic_json(summary_path, {"status": "ACK_FAILED", "reason": str(error)})
        return 2

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _atomic_json(summary_path, {
                "status": "OVERLAP_SKIPPED",
                "run_id": run_id,
                "ack_path": str(ack_path),
            })
            return 2

        # PID record: lets the independent watchdog detect and kill a hung
        # worker (which would otherwise hold the flock and silently starve
        # every later slot). Removed on any exit; the watchdog treats a stale
        # record whose summary exists as already-finished.
        pid_path = ROOT / f"logs/scheduler/{run_id}.pid"
        _atomic_json(pid_path, {
            "schema_version": 1,
            "pid": os.getpid(),
            "run_id": run_id,
            "kind": kind,
            "started_at": now.astimezone(timezone.utc).isoformat(),
            "scheduled_for": scheduled.astimezone(timezone.utc).isoformat(),
            "summary_path": str(summary_path),
        })
        try:
            return _execute_slot(
                run_id=run_id,
                kind=kind,
                symbol=symbol,
                scheduled=scheduled,
                now=now,
                ack_path=ack_path,
                log_root=log_root,
                summary_path=summary_path,
                pid_path=pid_path,
            )
        finally:
            # Remove the record only after a summary exists. A crash between
            # ack and summary leaves the record behind ON PURPOSE, so the
            # reaper files WORKER_DIED_NO_SUMMARY instead of the slot's loss
            # staying invisible until end of day.
            if summary_path.is_file():
                pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

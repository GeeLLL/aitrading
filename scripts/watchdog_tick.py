from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, time as time_of_day
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.authorization_watch import check_authorization_record
from monitoring.collection_observer import ensure_day_registered
from monitoring.daily_schedule import SESSION_TIMEZONE
from monitoring.market_calendar import is_market_open
from monitoring.power_watch import check_power
from monitoring.remote_alert import send_remote_alert
from monitoring.scheduler_watchdog import scan_expected_runs
from monitoring.worker_reaper import reap_overdue_workers


ALERT_DIR = ROOT / "logs/incidents/alerts"

# After this local (PT) time on a market day, the deterministic end-of-day
# report must exist; the watchdog writes it if the 13:05 CLOSE_SUMMARY slot
# failed to. Pure aggregation over local logs — this is a report, not a
# backfill: missing slots stay missing and are listed as such inside it.
EOD_FALLBACK_AFTER = time_of_day(13, 25)


def _notify(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    subprocess.run(
        [
            "/usr/bin/osascript", "-e",
            f'display notification "{safe_message}" with title "{safe_title}" sound name "Sosumi"',
        ],
        check=True,
        timeout=10,
    )


def deliver_pending_alerts(alert_directory: Path = ALERT_DIR) -> int:
    delivered = 0
    if not alert_directory.exists():
        return delivered
    sent = alert_directory / "sent"
    sent.mkdir(parents=True, exist_ok=True)
    for path in sorted(alert_directory.glob("*.alert.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _notify(str(payload["title"]), str(payload["message"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            continue
        # Best-effort push to the owner's phone as well (no-op without
        # config/alerting.json); the banner alone reaches nobody asleep.
        send_remote_alert(str(payload.get("title", "")), str(payload.get("message", "")))
        path.replace(sent / path.name)
        delivered += 1
    return delivered


def ensure_eod_report(now: datetime) -> bool:
    """Write the deterministic EOD report if the close slot failed to. Returns
    True when the report exists after this call."""
    local_now = now.astimezone(SESSION_TIMEZONE)
    today = local_now.date()
    if not is_market_open(today) or local_now.time() < EOD_FALLBACK_AFTER:
        return False
    report_path = ROOT / "logs/eod" / f"{today.isoformat()}.pnl.json"
    if report_path.is_file():
        return True
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/eod_report.py"), "--date", today.isoformat()],
        cwd=ROOT,
        timeout=120,
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0 and report_path.is_file()


def main() -> int:
    now = datetime.now().astimezone()
    # Close the "the Mac slept through every worker slot" gap: the watchdog runs
    # independently of the worker, so registering today's expectations here means
    # a slot that never fired still has an expectation on record and is flagged as
    # a miss below, instead of vanishing silently. Idempotent; no-ops when closed.
    ensure_day_registered(now, project_root=ROOT)
    results = scan_expected_runs(
        checked_at=now,
        expectation_directory=ROOT / "logs/scheduler/expected",
        ack_directory=ROOT / "logs/scheduler",
        incident_directory=ROOT / "logs/incidents",
    )
    incidents = [
        result for result in results
        if not result.health.healthy and result.health.reason != "START_ACK_PENDING"
    ]
    # A hung worker holds the collector flock and would starve every later
    # slot; reap it so only its own slot is lost. Never backfills.
    reaped = reap_overdue_workers(now, project_root=ROOT)
    # Forgery of the formal-Shadow authorization cannot be prevented in-process
    # (the pilot agent has arbitrary python3), but it must never be quiet.
    authorization = check_authorization_record(now, project_root=ROOT)
    # A mid-session drop to battery silently risks every remaining slot.
    power = check_power(now, project_root=ROOT)
    eod_written = ensure_eod_report(now)
    delivered = deliver_pending_alerts()
    print(json.dumps({
        "at": now.isoformat(),
        "status": "INCIDENT" if incidents else "HEALTHY",
        "expectations_checked": len(results),
        "incident_count": len(incidents),
        "alerts_delivered": delivered,
        "workers_reaped": [action["verdict"] for action in reaped],
        "authorization_watch": authorization.get("status"),
        "power": power.get("status"),
        "eod_report_present": eod_written,
    }, sort_keys=True))
    return 2 if (incidents or authorization.get("changed")) else 0


if __name__ == "__main__":
    raise SystemExit(main())

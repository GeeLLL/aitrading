from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.scheduler_watchdog import scan_expected_runs


ALERT_DIR = ROOT / "logs/incidents/alerts"


def _notify(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    subprocess.run(
        ["/usr/bin/osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
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
        path.replace(sent / path.name)
        delivered += 1
    return delivered


def main() -> int:
    results = scan_expected_runs(
        checked_at=datetime.now().astimezone(),
        expectation_directory=ROOT / "logs/scheduler/expected",
        ack_directory=ROOT / "logs/scheduler",
        incident_directory=ROOT / "logs/incidents",
    )
    incidents = [
        result for result in results
        if not result.health.healthy and result.health.reason != "START_ACK_PENDING"
    ]
    delivered = deliver_pending_alerts()
    print(json.dumps({
        "status": "INCIDENT" if incidents else "HEALTHY",
        "expectations_checked": len(results),
        "incident_count": len(incidents),
        "alerts_delivered": delivered,
    }, sort_keys=True))
    return 2 if incidents else 0


if __name__ == "__main__":
    raise SystemExit(main())

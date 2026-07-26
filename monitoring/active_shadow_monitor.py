#!/usr/bin/env python3
"""DEPRECATED entry point — now a thin shim over `collection_observer`.

This used to be a third monitor daemon that "attempted auto-recovery". Automatic
recovery of a missed market sample means backfilling it, which this experiment
forbids: a late read-only sample is not the observation that slot was meant to
capture. All health logic is consolidated into the single, pure
`monitoring.collection_observer`; this file only observes and reports.

Kept as a filename for backward compatibility; new code should import
`collection_observer` directly.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.collection_observer import observe_collection, render_report
from monitoring.daily_schedule import SESSION_TIMEZONE

CHECK_INTERVAL_SECONDS = 60


def report_once(now: datetime | None = None) -> str:
    moment = now or datetime.now(SESSION_TIMEZONE)
    return render_report(observe_collection(moment, project_root=ROOT))


def main() -> int:
    print("📡 active_shadow_monitor -> collection_observer (observe-only)")
    try:
        while True:
            print(report_once())
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

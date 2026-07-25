#!/usr/bin/env python3
"""Observe-only collection reporter (formerly the "robust sampling coordinator").

HISTORY / WHY THIS WAS GUTTED: the previous version of this file *backfilled*
missed market samples — its `auto_recover_from_failure` re-ran the worker for
slots that had already passed. That violates a core invariant of this
experiment: a missed market sample is never re-collected after the fact, because
a late read-only sample is not the sample that slot was supposed to observe.

This module now only *observes and reports*. All health logic lives in the single
`monitoring.collection_observer`, and market-hours logic comes from the corrected
`market_calendar`. There is deliberately no sampling execution, no retry, and no
backfill here. If a slot is missed, that shows up as a MISSED slot / incident for
a human to see — it is not silently papered over.
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
    """Return a one-shot human-readable status line (pure; no side effects)."""
    moment = now or datetime.now(SESSION_TIMEZONE)
    status = observe_collection(moment, project_root=ROOT)
    return render_report(status)


def main() -> int:
    print("📡 Collection observer (observe-only; never backfills)")
    print("=====================================================")
    try:
        while True:
            print(report_once())
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n✅ Observer stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

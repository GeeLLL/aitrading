#!/usr/bin/env python3
"""
Smart launchd worker: runs every 20 min but only executes on market-open days
and within registered time slots.

This replaces the old date-pinning approach. The plist runs this script every
1200 seconds (20 minutes) using StartInterval. This script then:

1. Checks: Is today a market-open day?
2. Checks: Is now() within a registered 20-minute time slot?
3. Only then: delegates to the real worker (launchd_shadow_worker.py)

If not a market day or not in a slot, exits silently (no logs, no errors).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import SESSION_TIMEZONE, DAILY_SLOTS
from monitoring.market_calendar import is_market_open_today


def _is_within_slot_window(now_time: datetime, slot_hour: int, slot_minute: int, window_minutes: int = 5) -> bool:
    """Check if current time is within ±window_minutes of a slot time."""
    slot_time = now_time.replace(hour=slot_hour, minute=slot_minute, second=0, microsecond=0)
    time_diff_seconds = abs((now_time - slot_time).total_seconds())
    return time_diff_seconds < (window_minutes * 60)


def main() -> int:
    """
    Smart entry point: decide whether to run the real worker.
    Exit codes:
      0 = exited without running (not a market day or wrong time)
      actual worker exit code = ran the worker
    """

    # Check 1: Is today a market-open day?
    if not is_market_open_today():
        # Not a market day. Exit silently.
        return 0

    # Check 2: Is now() within a registered time slot?
    now = datetime.now(SESSION_TIMEZONE)
    in_slot = False

    for (hour, minute), (kind, symbol) in DAILY_SLOTS.items():
        if _is_within_slot_window(now, hour, minute, window_minutes=5):
            in_slot = True
            break

    if not in_slot:
        # Not in a valid slot. Exit silently.
        return 0

    # We're on a market day and within a valid time slot.
    # Delegate to the real worker.
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/launchd_shadow_worker.py")],
        cwd=ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

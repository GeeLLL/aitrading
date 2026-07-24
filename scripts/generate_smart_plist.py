#!/usr/bin/env python3
"""
Generate a self-healing launchd plist that doesn't rely on date pinning.

Instead of hardcoding dates (Day=23, Day=24, etc), this generates a plist
with StartInterval=1200 (every 20 minutes) and lets the program decide
whether it should actually run.

This eliminates the need for nightly regeneration.
"""

from pathlib import Path
from datetime import datetime, timezone
import sys

PLIST_TEMPLATE = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.robinhood-ai-trader.shadow-worker-v2</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13</string>
    <string>/Users/ge/ge/aitrading/scripts/launchd_shadow_worker.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/ge/ge/aitrading</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/Users/ge/.local/bin:/Users/ge/.claude/local:/usr/bin:/bin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/ge/ge/aitrading/logs/launchd-worker.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/ge/ge/aitrading/logs/launchd-worker.stderr.log</string>
  <key>StartInterval</key>
  <integer>1200</integer>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
</dict>
</plist>
'''

SMART_WORKER_WRAPPER = '''#!/usr/bin/env python3
"""
Smart launchd worker: runs every 20 min but only executes on market open days.

This replaces the old date-pinning approach. The worker runs frequently but
checks: is_market_open_today() before actually doing work.

If not a market day, it exits silently (0 return code, no log).
"""

import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import SESSION_TIMEZONE, DAILY_SLOTS
from monitoring.market_calendar import is_market_open_today

def main() -> int:
    """
    Smart entry point: check if we should run, then delegate to the real worker.
    """

    # Check: is today a market open day?
    if not is_market_open_today():
        # Not a market day (weekend, holiday, etc).
        # Exit silently. No log, no error, no fuss.
        return 0

    # Check: is now() within a registered time slot?
    now = datetime.now(SESSION_TIMEZONE)

    # Find which slot we're in (allow ±5 min window)
    in_slot = False
    for (hour, minute), (kind, symbol) in DAILY_SLOTS.items():
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        time_diff = abs((now - slot_time).total_seconds())
        if time_diff < 300:  # Within 5 minutes of a scheduled slot
            in_slot = True
            break

    if not in_slot:
        # Not in a scheduled slot. Exit silently.
        return 0

    # We're in a valid slot on a market day. Delegate to the real worker.
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/launchd_shadow_worker.py")],
        cwd=ROOT,
    )
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
'''

def generate_plist() -> str:
    """Generate the smart plist content."""
    return PLIST_TEMPLATE

def generate_worker_wrapper() -> str:
    """Generate the smart worker wrapper."""
    return SMART_WORKER_WRAPPER

if __name__ == "__main__":
    # Write plist
    plist_path = Path.home() / "Library/LaunchAgents/com.robinhood-ai-trader.shadow-worker-v2.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(generate_plist(), encoding="utf-8")
    print(f"✅ Generated plist: {plist_path}")

    # Write worker wrapper
    worker_path = Path("/Users/ge/ge/aitrading/scripts/launchd_shadow_worker_smart.py")
    worker_path.write_text(generate_worker_wrapper(), encoding="utf-8")
    worker_path.chmod(0o755)
    print(f"✅ Generated smart worker: {worker_path}")

    # Reload launchd
    print("Reloading launchd...")
    import subprocess
    import os
    uid = os.getuid()

    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
        stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["sleep", "1"]
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)]
    )

    print("✅ launchd reloaded")
    print("\n📝 NOTE: This plist no longer uses date pinning!")
    print("   - Runs every 20 minutes")
    print("   - But only executes on market-open days")
    print("   - No nightly cron job needed anymore")

#!/usr/bin/env python3
"""Render the *recurring, self-arming* launchd worker plist.

Unlike ``generate_shadow_worker_plist.py`` (which pins every entry to a single
``Month``/``Day`` and therefore must be regenerated and re-loaded by hand every
morning), this plist's ``StartCalendarInterval`` entries carry only ``Hour`` and
``Minute``. launchd fires each slot every day, forever — no nightly re-arming,
which was the dominant cause of the week-long data gap.

Every fire runs ``self_arming_worker.py``, which:
  * no-ops cleanly on weekends / holidays / after an early close (so a recurring
    schedule never acts on a closed market),
  * auto-registers the day's expectations (so the watchdog sees any miss), and
  * delegates to the real, safety-gating worker.

No ``KeepAlive``: each StartCalendarInterval fire runs once and exits cleanly, so
a "closed day" or "not my slot" exit never respawn-loops. When the Mac sleeps
through a slot, launchd replays that missed calendar event once on wake; the real
worker's 180s freshness guard then REFUSES the stale fire rather than backfilling.

Every path is derived from the running interpreter and the repo location, so the
plist is correct wherever the repository lives.

Usage:
    python3 scripts/generate_self_arming_plist.py > /tmp/self-arming-worker.plist

Then, on the Mac, retire any old date-pinned worker and load this one (single
job, single Label):
    launchctl bootout gui/$(id -u)/com.robinhood-ai-trader.shadow-worker-v2 2>/dev/null
    launchctl bootout gui/$(id -u)/com.robinhood-ai-trader.self-arming-worker 2>/dev/null
    cp /tmp/self-arming-worker.plist ~/Library/LaunchAgents/com.robinhood-ai-trader.self-arming-worker.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.robinhood-ai-trader.self-arming-worker.plist

The watchdog service (generate_watchdog_plist.py) is still installed separately.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS
from monitoring.launchd_paths import launch_path

LABEL = "com.robinhood-ai-trader.self-arming-worker"


def render(*, python: Path | None = None, workdir: Path | None = None) -> str:
    python_path = (python or Path(sys.executable)).resolve()
    work = (workdir or ROOT).resolve()
    worker = work / "scripts/self_arming_worker.py"
    launch_path_value = launch_path(python_path)

    intervals = []
    for hour, minute in sorted(DAILY_SLOTS):
        intervals.append(
            "    <dict>\n"
            f"      <key>Hour</key><integer>{hour}</integer>\n"
            f"      <key>Minute</key><integer>{minute}</integer>\n"
            "    </dict>"
        )
    entries = "\n".join(intervals)
    py = escape(str(python_path))
    wk = escape(str(work))
    wkr = escape(str(worker))
    path_value = escape(launch_path_value)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{wkr}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{wk}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_value}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>{wk}/logs/self-arming-worker.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>{wk}/logs/self-arming-worker.stderr.log</string>
  <key>RunAtLoad</key>
  <false/>
  <key>StartCalendarInterval</key>
  <array>
{entries}
  </array>
</dict>
</plist>
"""


def main() -> int:
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

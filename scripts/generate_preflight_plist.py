#!/usr/bin/env python3
"""Render the launchd preflight plist for this host.

The preflight runs at 05:45 PT on weekdays — 25 minutes before the first
collection slot — and proves the unattended chain (launchd jobs, Claude CLI,
MCP OAuth, AC power, disk, safety gate) is green, alerting loudly otherwise.
Paths are derived from the actual interpreter and repo location, same as the
other generators.

Usage:
    python3 scripts/generate_preflight_plist.py > ~/Library/LaunchAgents/com.robinhood-ai-trader.preflight.plist
    launchctl bootout gui/$(id -u)/com.robinhood-ai-trader.preflight 2>/dev/null
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.robinhood-ai-trader.preflight.plist
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.launchd_paths import launch_path

LABEL = "com.robinhood-ai-trader.preflight"
HOUR = 5
MINUTE = 45
WEEKDAYS = (1, 2, 3, 4, 5)  # Monday-Friday


def render(*, python: Path | None = None, workdir: Path | None = None) -> str:
    python_path = (python or Path(sys.executable)).resolve()
    work = (workdir or ROOT).resolve()
    script = work / "scripts/preflight_check.py"
    py = escape(str(python_path))
    wk = escape(str(work))
    sc = escape(str(script))
    path_value = escape(launch_path(python_path))
    intervals = "\n".join(
        f"""    <dict>
      <key>Weekday</key>
      <integer>{weekday}</integer>
      <key>Hour</key>
      <integer>{HOUR}</integer>
      <key>Minute</key>
      <integer>{MINUTE}</integer>
    </dict>"""
        for weekday in WEEKDAYS
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{sc}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{wk}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_value}</string>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StartCalendarInterval</key>
  <array>
{intervals}
  </array>
  <key>StandardOutPath</key>
  <string>{wk}/logs/preflight.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>{wk}/logs/preflight.stderr.log</string>
</dict>
</plist>
"""


def main() -> int:
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

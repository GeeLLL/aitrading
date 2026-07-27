#!/usr/bin/env python3
"""Pre-market preflight (05:45 PT weekdays, via launchd).

Proves the whole unattended chain is healthy BEFORE the 06:10 canary, so an
auth/power/scheduling problem surfaces while there is still time to fix it,
instead of inside the market window. Checks:

  1. launchd jobs loaded (self-arming worker + watchdog)
  2. Claude CLI present
  3. MCP end-to-end probe: one real read-only get_equity_quotes call through
     the Claude CLI (proves OAuth token refresh works unattended)
  4. On AC power (a battery morning killed a whole week once)
  5. Disk space
  6. Order-safety gate evaluates green (READ_ONLY / kill switch engaged)

Failures produce a macOS banner WITH SOUND and a persistent JSON record under
logs/preflight/. On market days it also spawns a detached ``caffeinate`` that
holds the Mac awake through the close window (belt-and-braces against sleep).

Read-only besides its own log files. No environment-variable bypass exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from monitoring.daily_schedule import SESSION_TIMEZONE
from monitoring.market_calendar import is_market_open
from monitoring.remote_alert import send_remote_alert

REQUIRED_JOBS = (
    "com.robinhood-ai-trader.self-arming-worker",
    "com.robinhood-ai-trader.watchdog",
)
PROBE_TIMEOUT_SECONDS = 180
MIN_DISK_GB = 2
CAFFEINATE_UNTIL = (13, 25)  # PT


def _notify(title: str, message: str) -> None:
    safe_title = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    subprocess.run(
        [
            "/usr/bin/osascript", "-e",
            f'display notification "{safe_message}" with title "{safe_title}" sound name "Sosumi"',
        ],
        check=False,
        timeout=10,
    )


def check_jobs_loaded() -> tuple[bool, str]:
    uid = os.getuid()
    missing = []
    for label in REQUIRED_JOBS:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            missing.append(label)
    return (not missing, f"missing: {', '.join(missing)}" if missing else "both loaded")


def check_claude_cli() -> tuple[bool, str]:
    try:
        from execution.official_mcp_collector import claude_binary
        return True, claude_binary()
    except Exception as error:  # noqa: BLE001 — report anything, fail closed
        return False, f"{type(error).__name__}: {error}"


def check_mcp_probe() -> tuple[bool, str]:
    """One real read-only MCP call through the CLI: proves token + transport."""
    try:
        from execution.official_mcp_collector import claude_binary
        binary = claude_binary()
    except Exception as error:  # noqa: BLE001
        return False, f"no CLI: {error}"
    prompt = (
        "Call the mcp__robinhood-trading__get_equity_quotes tool exactly once "
        "for symbol SPY. If the tool returns quote data, output exactly "
        "PROBE_OK. If the tool errors, output the error text. Output nothing else."
    )
    try:
        completed = subprocess.run(
            [
                binary, "-p",
                "--allowedTools", "mcp__robinhood-trading__get_equity_quotes",
                "--disallowedTools", "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch,Task",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {PROBE_TIMEOUT_SECONDS}s"
    except OSError as error:
        return False, f"{type(error).__name__}: {error}"
    if completed.returncode == 0 and "PROBE_OK" in completed.stdout:
        return True, "PROBE_OK"
    detail = (completed.stdout or completed.stderr or "").strip()[:300]
    return False, f"exit={completed.returncode} output={detail!r}"


def check_ac_power() -> tuple[bool, str]:
    result = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
    head = result.stdout.splitlines()[0] if result.stdout else ""
    return ("AC Power" in head, head.strip() or "pmset unavailable")


def check_disk() -> tuple[bool, str]:
    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / (1024 ** 3)
    return free_gb >= MIN_DISK_GB, f"{free_gb:.1f} GiB free"


def check_safety_gate() -> tuple[bool, str]:
    try:
        from main import build_status
        status = build_status()
        ok = (
            status.get("system_mode") == "READ_ONLY"
            and status.get("live_trading_enabled") is False
            and status.get("order_tools_enabled") is False
            and status.get("kill_switch_engaged") is True
        )
        return ok, status.get("system_mode", "UNKNOWN")
    except Exception as error:  # noqa: BLE001 — a crashing gate is a failed gate
        return False, f"{type(error).__name__}: {error}"


def spawn_caffeinate(now: datetime) -> int | None:
    """Hold the Mac awake through the close window. Detached; idempotent enough
    (an extra assertion is harmless and expires on its own)."""
    local = now.astimezone(SESSION_TIMEZONE)
    until = local.replace(hour=CAFFEINATE_UNTIL[0], minute=CAFFEINATE_UNTIL[1], second=0)
    seconds = int((until - local).total_seconds())
    if seconds <= 0:
        return None
    process = subprocess.Popen(
        ["caffeinate", "-ims", "-t", str(seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process.pid


def main() -> int:
    now = datetime.now(SESSION_TIMEZONE)
    today = now.date()
    market_day = is_market_open(today)
    checks: dict[str, dict[str, object]] = {}

    def run(name: str, fn) -> bool:
        ok, detail = fn()
        checks[name] = {"ok": ok, "detail": detail}
        return ok

    all_ok = True
    all_ok &= run("launchd_jobs_loaded", check_jobs_loaded)
    all_ok &= run("claude_cli", check_claude_cli)
    all_ok &= run("ac_power", check_ac_power)
    all_ok &= run("disk_space", check_disk)
    all_ok &= run("safety_gate", check_safety_gate)
    if market_day:
        all_ok &= run("mcp_probe", check_mcp_probe)
        caffeinate_pid = spawn_caffeinate(now)
    else:
        checks["mcp_probe"] = {"ok": None, "detail": "skipped: market closed"}
        caffeinate_pid = None

    record = {
        "schema_version": 1,
        "date": today.isoformat(),
        "checked_at": now.isoformat(),
        "market_day": market_day,
        "all_ok": bool(all_ok),
        "checks": checks,
        "caffeinate_pid": caffeinate_pid,
    }
    out_dir = ROOT / "logs/preflight"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}.json"
    temporary = out_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(out_path)
    print(json.dumps({"all_ok": bool(all_ok), "path": str(out_path)}))

    if market_day and not all_ok:
        failed = ", ".join(name for name, check in checks.items() if check["ok"] is False)
        message = f"{failed} — 06:10 首个槽位前需人工处理 (logs/preflight/{today.isoformat()}.json)"
        _notify("Robinhood 采集预检失败", message)
        send_remote_alert("Robinhood 采集预检失败", message)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

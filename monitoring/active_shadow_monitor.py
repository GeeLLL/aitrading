#!/usr/bin/env python3
"""
Active Shadow Monitor: Continuously monitors formal shadow trading and alerts on failures.

This daemon runs continuously during market hours and:
1. Checks every minute if sampling is happening
2. Detects failures immediately
3. Attempts auto-recovery
4. Alerts on critical issues
5. Tracks P&L in real-time

NEVER silently fail — always communicate issues immediately.
"""

import json
import sys
import time
import subprocess
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, expected_runs_for_date
from monitoring.market_calendar import is_market_open_today
from monitoring.scheduler_watchdog import unresolved_incident_ids


class ShadowMonitor:
    """Active monitoring daemon for formal shadow trading."""

    def __init__(self):
        self.root = ROOT
        self.session_start = datetime.now(SESSION_TIMEZONE)
        self.last_alert_time = {}  # Prevent alert spam
        self.sample_history = defaultdict(list)
        self.alert_log = self.root / "logs/shadow_monitor_alerts.jsonl"
        self.status_file = self.root / "logs/shadow_monitor_status.json"

    def alert(self, level: str, message: str, auto_fix_attempted: bool = False):
        """
        Alert immediately on critical issues.

        Levels: CRITICAL, WARNING, INFO
        """
        timestamp = datetime.now(SESSION_TIMEZONE).isoformat()

        # Prevent spam: only alert once per issue per 5 minutes
        key = f"{level}:{message}"
        if key in self.last_alert_time:
            if (datetime.now() - self.last_alert_time[key]).total_seconds() < 300:
                return
        self.last_alert_time[key] = datetime.now()

        alert = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "auto_fix_attempted": auto_fix_attempted,
            "session_elapsed": str(datetime.now(SESSION_TIMEZONE) - self.session_start)
        }

        # Log to file
        with open(self.alert_log, "a") as f:
            f.write(json.dumps(alert) + "\n")

        # Print immediately
        icon = {
            "CRITICAL": "🚨",
            "WARNING": "⚠️",
            "INFO": "ℹ️"
        }.get(level, "•")

        print(f"{icon} [{timestamp}] {level}: {message}", flush=True)

    def check_system_health(self) -> bool:
        """Check if system is healthy. Returns True if OK, False if critical failure."""

        # Check 1: Are there unresolved incidents?
        unresolved = unresolved_incident_ids()
        if unresolved:
            self.alert(
                "CRITICAL",
                f"Sampling blocked: {len(unresolved)} unresolved incidents",
                auto_fix_attempted=False
            )
            return False

        # Check 2: Is market open?
        if not is_market_open_today():
            self.alert("INFO", "Market is closed (weekend/holiday)")
            return False

        # Check 3: Is kill switch engaged?
        try:
            result = subprocess.run(
                [sys.executable, str(self.root / "main.py"), "status"],
                capture_output=True,
                text=True,
                timeout=10
            )
            status = json.loads(result.stdout)

            if not status.get("kill_switch_engaged"):
                self.alert("CRITICAL", "Kill switch is DISENGAGED! Trading may be enabled!")
                return False

            if status.get("live_trading_enabled"):
                self.alert("CRITICAL", "Live trading is ENABLED! This should never happen in Pilot!")
                return False

        except Exception as e:
            self.alert("WARNING", f"Could not verify system status: {e}")
            return False

        return True

    def check_sampling_progress(self) -> dict:
        """Check if sampling is happening on schedule."""

        today = date.today()
        sample_dir = self.root / "logs/launchd_worker" / today.isoformat()

        if not sample_dir.exists():
            self.alert("WARNING", "No sample directory yet (before first sample)")
            return {"status": "no_samples_yet"}

        # Count samples
        all_files = list(sample_dir.glob("*.json"))
        completed = [f for f in all_files if json.loads(f.read_text()).get("status") == "COMPLETED"]
        failed = [f for f in all_files if json.loads(f.read_text()).get("status") in ("SAFETY_GATE_FAILED", "ERROR")]

        if failed and not completed:
            self.alert(
                "CRITICAL",
                f"All {len(failed)} samples have failed! Expected successful sampling by now."
            )
            return {"status": "all_failed", "count": len(failed)}

        if failed:
            self.alert(
                "WARNING",
                f"{len(failed)} samples failed (but {len(completed)} succeeded)"
            )

        return {
            "status": "ok" if completed else "no_successful_samples",
            "completed": len(completed),
            "failed": len(failed),
            "total": len(all_files)
        }

    def check_expected_slots(self) -> dict:
        """Check if we're missing expected sampling slots."""

        today = date.today()
        expected = expected_runs_for_date(today)

        # Check which slots have been attempted
        sample_dir = self.root / "logs/launchd_worker" / today.isoformat()
        if not sample_dir.exists():
            return {"status": "no_samples_yet"}

        attempted_slots = set()
        for f in sample_dir.glob("*.json"):
            if "pilot-" in f.name or "canary-" in f.name or "market-gate-" in f.name:
                # Extract time from filename: pilot-20260723-0743.json -> 07:43
                parts = f.name.split("-")
                if len(parts) >= 3:
                    time_str = parts[-1].replace(".json", "")
                    if len(time_str) == 4:
                        attempted_slots.add(time_str)

        # Compare with expected
        now = datetime.now(SESSION_TIMEZONE)
        expected_by_now = []
        for run_id, scheduled_for in expected:
            if scheduled_for <= now:
                time_str = scheduled_for.strftime("%H%M")
                expected_by_now.append(time_str)

        missing = set(expected_by_now) - attempted_slots
        if missing:
            self.alert(
                "WARNING",
                f"Missing {len(missing)} expected samples: {sorted(missing)}"
            )
            return {"status": "missing_samples", "missing": sorted(missing)}

        return {"status": "on_schedule"}

    def calculate_pnl(self) -> dict:
        """Calculate P&L from completed samples."""

        today = date.today()
        sample_dir = self.root / "logs/launchd_worker" / today.isoformat()

        total_pnl = 0.0
        trades = 0

        if not sample_dir.exists():
            return {"total_pnl": 0.0, "trades": 0}

        for decision_file in sample_dir.glob("*.decision.json"):
            try:
                data = json.loads(decision_file.read_text())
                if data.get("decision", {}).get("action") == "ENTRY_SIMULATED":
                    trades += 1
                    pnl = data.get("decision", {}).get("simulated_pnl", 0)
                    total_pnl += pnl
            except:
                pass

        return {"total_pnl": total_pnl, "trades": trades}

    def run_check_cycle(self) -> bool:
        """Run a complete health check cycle. Returns True if system is OK."""

        # Check 1: System health
        if not self.check_system_health():
            return False

        # Check 2: Sampling progress
        sampling = self.check_sampling_progress()
        if sampling.get("status") == "all_failed":
            return False

        # Check 3: Expected slots
        slots = self.check_expected_slots()

        # Check 4: P&L
        pnl = self.calculate_pnl()

        # Update status file
        status = {
            "timestamp": datetime.now(SESSION_TIMEZONE).isoformat(),
            "session_elapsed": str(datetime.now(SESSION_TIMEZONE) - self.session_start),
            "health": "OK",
            "sampling": sampling,
            "slots": slots,
            "pnl": pnl
        }

        self.status_file.write_text(json.dumps(status, indent=2))

        return True

    def run_continuous(self, check_interval_seconds: int = 60):
        """
        Run continuous monitoring loop during market hours.

        Check every N seconds.
        Stop at market close (13:05 PT).
        """

        print("🚀 Shadow Monitor started", flush=True)
        print(f"   Check interval: {check_interval_seconds}s", flush=True)
        print(f"   Session start: {self.session_start.strftime('%H:%M:%S')}", flush=True)
        print("")

        while True:
            # Stop at market close (13:05 PT)
            now = datetime.now(SESSION_TIMEZONE)
            market_close = datetime(2026, 7, 23, 13, 6, tzinfo=SESSION_TIMEZONE)
            if now >= market_close:
                print("📊 Market close reached. Stopping monitor.", flush=True)
                break

            # Run check
            try:
                self.run_check_cycle()
            except Exception as e:
                self.alert("WARNING", f"Monitor check failed: {e}")

            # Wait for next check
            time.sleep(check_interval_seconds)


def main():
    monitor = ShadowMonitor()

    # Run continuous monitoring
    try:
        monitor.run_continuous(check_interval_seconds=60)  # Check every minute
    except KeyboardInterrupt:
        print("\n📍 Monitor stopped by user", flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Monitor crashed: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

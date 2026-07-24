#!/bin/bash
# Auto-register expected runs for today (market-open days only)
#
# Run this at 5:00 AM PT every weekday (launchd will handle weekdays-only scheduling)
# This replaces the manual "python3 main.py scheduler-expect-day" command

REPO_ROOT="/Users/ge/ge/aitrading"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${REPO_ROOT}/logs/daily_expectation_registration.log"

mkdir -p "$(dirname "$LOG_FILE")"

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Registering expectations for $TODAY..."

    cd "$REPO_ROOT" || exit 1

    # Check if today is a market-open day (Python check)
    if ! python3 << 'EOF'
from monitoring.market_calendar import is_market_open_today
exit(0 if is_market_open_today() else 1)
EOF
    then
        echo "[$(date)] → Not a market day, skipping"
        exit 0
    fi

    # Register today's expected runs
    python3 << 'EOF'
from monitoring.scheduler_watchdog import register_expected_run
from monitoring.daily_schedule import expected_runs_for_date
from datetime import date
import sys

today = date.today()
expected_runs = expected_runs_for_date(today)

print(f"Registering {len(expected_runs)} expected runs for {today}...")

success = 0
for run_id, scheduled_for in expected_runs:
    try:
        register_expected_run(run_id=run_id, scheduled_for=scheduled_for)
        success += 1
    except Exception as e:
        print(f"  ⚠️  {run_id}: {e}")

print(f"✅ {success}/{len(expected_runs)} runs registered")
sys.exit(0 if success == len(expected_runs) else 1)
EOF

    if [ $? -eq 0 ]; then
        echo "[$(date)] → ✅ Registration successful"
        exit 0
    else
        echo "[$(date)] → ❌ Registration failed"
        exit 1
    fi

} >> "$LOG_FILE" 2>&1

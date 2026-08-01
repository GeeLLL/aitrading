"""Rebuild dashboard/index.html for the 2026-07-29 CLOSE_SUMMARY run.

Local logs only. Updates the embedded shadow-data JSON in place: regenerates the
timestamp, recounts receipt/expectation/incident metrics from logs/, prepends this
close run to the run table, and attaches the day's close block.

The `latest` object is deliberately LEFT as the 11:23 slot: it is the most recent
real market observation of the day, and this close run made no MCP call, so
overwriting it would either blank the market panels or imply fresh market data.
"""
import glob
import json
import os
import re
import subprocess

HTML = "dashboard/index.html"
MARK_OPEN = '<script id="shadow-data" type="application/json">'
MARK_CLOSE = "</script>"

now_local = subprocess.run(
    ["date", "+%Y-%m-%dT%H:%M:%S%z"], capture_output=True, text=True
).stdout.strip()
# 2026-07-29T13:07:11-0700 -> 2026-07-29T13:07:11-07:00
now_local = now_local[:-2] + ":" + now_local[-2:]

html = open(HTML).read()
start = html.index(MARK_OPEN) + len(MARK_OPEN)
end = html.index(MARK_CLOSE, start)
data = json.loads(html[start:end])

# --- recount from local logs -------------------------------------------------
receipts = [
    f
    for f in glob.glob("logs/launchd_worker/*/*.json")
    if os.path.basename(f).endswith(".summary.json")
    or os.path.basename(f).startswith("launchd-canary")
]
completed = 0
for f in receipts:
    try:
        st = str(json.load(open(f)).get("status"))
    except Exception:
        continue
    if st in ("SUCCESS", "COMPLETED", "SUCCESS_WITH_DISCLOSED_SELECTION_ERROR",
              "COMPLETED_WITH_FAILED_CHECK"):
        completed += 1

expected_runs = len(glob.glob("logs/scheduler/expected/*.expected.json"))
acked_runs = len(glob.glob("logs/scheduler/*.start.json"))
incident_files = len(glob.glob("logs/incidents/*.scheduler-incident.json"))

eod = json.load(open("logs/eod/2026-07-29.pnl.json"))
close = json.load(
    open("logs/launchd_worker/2026-07-29/pilot-close-canary-20260729-1305.summary.json")
)

# --- metrics -----------------------------------------------------------------
m = data["metrics"]
m["completed_runs"] = completed
m["virtual_trades"] = eod["pnl"]["policy"]["filled_and_exited"]
m["no_trades"] = 14  # today's 14 PILOT_SAMPLE slots, every one NO_TRADE
m["latest_outcome"] = "NO_TRADE"
m["expected_runs"] = expected_runs
m["acknowledged_expected_runs"] = acked_runs
m["scheduler_incidents"] = incident_files
m["active_runs"] = 0

# --- research ----------------------------------------------------------------
r = data["research"]
r["eligible_runs"] = 0
r["ineligible_runs"] = completed
# 5 qualified underlying signals today: BAC + NVDA at 07:03, MSFT + AMZN + META at 10:23.
r["mechanical_signals"] = 5
# None survived contract eligibility (option-volume floor and the $75 stage-1 premium cap).
r["strict_candidates"] = 0
# top_rejections is a cumulative aggregate and is left untouched: this local-only
# close run does not recompute it, so overwriting it would fabricate a number.

data["active"] = []
data["generated_at"] = now_local

# --- run table ---------------------------------------------------------------
run_row = {
    "run_id": close["run_id"],
    "run_kind": "CLOSE_SUMMARY",
    "scheduled_time": close["scheduled_for"],
    "completed_at": close["generated_at"],
    "status": "COMPLETED",
    "decision": "CLOSE_SUMMARY_NO_TRADE_DAY",
    "evidence_class": close["evidence_class"],
    "schema_version": 1,
    "mcp_calls": 0,
    "dashboard_rebuilt": True,
    "decision_pipeline": {
        "final_outcome": "NO_TRADE",
        "mechanical_signal_count": 5,
        "option_research_status": "NOT_RUN_LOCAL_LOGS_ONLY",
        "virtual_position_created": False,
    },
    "governance": {"performance_eligibility": "PILOT_EXCLUDED_FROM_PERFORMANCE"},
    "option_research": {"quote_samples": 0},
    "market": {"regime": "LOCAL_LOGS_ONLY"},
    "run_duration_seconds": None,
}
data["runs"] = [run_row] + [
    x for x in data.get("runs", []) if x.get("run_id") != close["run_id"]
]

# --- durable close block (not rendered; kept for the owner) -------------------
data["close_summary"] = {
    "observation_date": "2026-07-29",
    "result": close["result"],
    "slot_coverage": {
        "expected": eod["slot_coverage"]["expected"],
        "completed": eod["slot_coverage"]["completed"],
        "missed": eod["slot_coverage"]["missed"],
        "note": close["schedule_reconciliation"]["failed_slot_explanation"],
    },
    "trajectories": {
        "distinct": 21,
        "incomplete": [],
        "simulated_fills": 0,
        "fill_windows_opened": 3,
    },
    "calibration_trade": close["calibration_trade"]["deterministic_pnl"],
    "market_gate": {
        "verdict": "NOT_QUALIFIED",
        "passed": 5,
        "failed": 1,
        "failing_check": "official_instrument_session",
    },
    "owner_actions_required": close["owner_actions_required"],
    "close_note": close["close_note"],
    "latest_object_note": (
        "The `latest` object below is the 11:23 pilot slot, the last real market observation of "
        "2026-07-29. This close run made zero MCP calls and did not refresh market data."
    ),
}

out = html[:start] + json.dumps(data, ensure_ascii=False) + html[end:]
with open(HTML, "w") as fh:
    fh.write(out)

print(json.dumps({
    "status": "REBUILT",
    "path": HTML,
    "bytes": os.path.getsize(HTML),
    "generated_at": now_local,
    "completed_runs": completed,
    "expected_runs": expected_runs,
    "acknowledged": acked_runs,
    "scheduler_incidents": incident_files,
}))

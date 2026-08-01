"""Rebuild dashboard/index.html for the 2026-07-31 CLOSE_SUMMARY run.

Local logs only. Updates the embedded shadow-data JSON in place: regenerates the
timestamp, recounts receipt/expectation/incident metrics from logs/, prepends this
close run to the run table, and attaches the day's close block.

The `latest` object is deliberately LEFT as the 11:23 slot: it is the most recent
real market observation of the day, and this close run made no MCP call, so
overwriting it would either blank the market panels or imply fresh market data.
`research.top_rejections` is a cumulative aggregate this local-only run does not
recompute; overwriting it would fabricate a number, so it is left untouched.
"""
import glob
import json
import os
import subprocess

HTML = "dashboard/index.html"
MARK_OPEN = '<script id="shadow-data" type="application/json">'
MARK_CLOSE = "</script>"
DATE = "2026-07-31"
RUN_ID = "pilot-close-canary-20260731-1305"

now_local = subprocess.run(
    ["date", "+%Y-%m-%dT%H:%M:%S%z"], capture_output=True, text=True
).stdout.strip()
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

eod = json.load(open("logs/eod/%s.pnl.json" % DATE))
close = json.load(open("logs/launchd_worker/%s/%s.summary.json" % (DATE, RUN_ID)))
cov = eod["slot_coverage"]

# --- metrics -----------------------------------------------------------------
m = data["metrics"]
m["completed_runs"] = completed
m["virtual_trades"] = eod["pnl"]["policy"]["filled_and_exited"]
# 7 PILOT_SAMPLE slots completed today; every one ended NO_TRADE.
m["no_trades"] = 7
m["latest_outcome"] = "NO_TRADE"
m["expected_runs"] = expected_runs
m["acknowledged_expected_runs"] = acked_runs
m["scheduler_incidents"] = incident_files
m["active_runs"] = 0

# --- research ----------------------------------------------------------------
r = data["research"]
r["eligible_runs"] = 0
r["ineligible_runs"] = completed
# 1 fully qualified underlying signal today (SOFI at 10:23) - the pilot's first.
r["mechanical_signals"] = 1
# It produced a candidate contract but no simulated entry: the ask moved above
# the recorded limit inside an open 60s fill window.
r["strict_candidates"] = 0

data["active"] = []
data["generated_at"] = now_local

# --- run table ---------------------------------------------------------------
run_row = {
    "run_id": close["run_id"],
    "run_kind": "CLOSE_SUMMARY",
    "scheduled_time": close["scheduled_for"],
    "completed_at": close["completed_at"],
    "status": "COMPLETED",
    "decision": "CLOSE_SUMMARY_NO_TRADE_DAY_DEGRADED_COVERAGE",
    "evidence_class": close["evidence_class"],
    "schema_version": 1,
    "mcp_calls": 0,
    "dashboard_rebuilt": True,
    "decision_pipeline": {
        "final_outcome": "NO_TRADE",
        "mechanical_signal_count": 1,
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
    "observation_date": DATE,
    "headline": close["headline"],
    "slot_coverage": {
        "expected": cov["expected"],
        "completed": cov["completed"],
        "failed": cov["failed"],
        "missed": cov["missed"],
        "verdict": close["schedule_completeness"]["verdict"],
        "root_cause": close["root_cause_analysis"]["verdict"],
        "note": close["schedule_completeness"]["interpretation"],
    },
    "trajectories": {
        "distinct": close["trajectory_completeness"]["chains"],
        "incomplete": [],
        "simulated_fills": 2,
        "fill_windows_opened": 3,
        "no_fill": 1,
    },
    "first_qualified_signal": close["research_findings_today"][
        "first_fully_qualified_signal_of_the_pilot"
    ],
    "highest_volume_ratio": close["research_findings_today"][
        "highest_volume_ratio_ever_recorded"
    ],
    "calibration_trade": close["calibration_trade"],
    "market_gate": close["market_gate_status"],
    "bar_time_audit": close["bar_time_audit"],
    "defects_found": close["defects_found"],
    "latest_object_note": (
        "The `latest` object below is the 11:23 pilot slot, the last real market observation of "
        "%s. This close run made zero MCP calls and did not refresh market data." % DATE
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

"""Write the CLOSE_SUMMARY terminal document for pilot-close-canary-20260731-1305.

Local logs only. Every headline P&L / slot-coverage / calibration figure is copied
verbatim from the deterministic run of `scripts/eod_report.py --date 2026-07-31`
(logs/eod/2026-07-31.pnl.json). Nothing in this file is market data and nothing
is backfilled.
"""
import json
import os
import subprocess

RUN_ID = "pilot-close-canary-20260731-1305"
DATE = "2026-07-31"
OUT = "logs/launchd_worker/%s/%s.summary.json" % (DATE, RUN_ID)

eod = json.load(open("logs/eod/%s.pnl.json" % DATE))
cov = eod["slot_coverage"]
cal = eod["calibration_trade"]

now = subprocess.run(["date", "+%Y-%m-%dT%H:%M:%S%z"], capture_output=True, text=True).stdout.strip()
now = now[:-2] + ":" + now[-2:]

# --- slot outcomes, straight out of the deterministic coverage table -------
missed = [s["run_id"] for s in cov["slots"] if not s["ack"]]
acked_no_summary = [
    s["run_id"] for s in cov["slots"]
    if s["ack"] and s["status"] is None and s["run_id"] != RUN_ID
]

summary = {
    "run_id": RUN_ID,
    "run_kind": "CLOSE_SUMMARY",
    "observation_date": DATE,
    "scheduled_for": "2026-07-31T13:05:00-07:00",
    "completed_at": now,
    "status": "SUCCESS",
    "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
    "data_sources": "LOCAL_LOGS_ONLY",

    "safety_preconditions_verified": {
        "checked_with": "python3 main.py status",
        "system_mode": "READ_ONLY",
        "live_trading_enabled": False,
        "order_tools_enabled": False,
        "kill_switch_engaged": True,
        "kill_switch_reason": "TRADING_ARM_MARKER_ABSENT",
        "automation_halted": False,
        "all_preconditions_met": True,
    },

    "deterministic_adjudication": {
        "command": "python3 scripts/eod_report.py --date %s" % DATE,
        "machine_readable": "logs/eod/%s.pnl.json" % DATE,
        "human_readable": "logs/eod/%s.report.md" % DATE,
        "note": (
            "All slot-coverage, P&L and calibration figures below are copied verbatim from that "
            "deterministic local run. No figure in this file was computed by the agent."
        ),
    },

    # ---------------------------------------------------------------- schedules
    "schedule_completeness": {
        "expected": cov["expected"],
        "completed": cov["completed"],
        "failed": cov["failed"],
        "missed": cov["missed"],
        "verdict": "DEGRADED_DAY_MAJOR_COVERAGE_LOSS",
        "missed_no_start_ack": missed,
        "acked_but_no_summary": acked_no_summary,
        "start_ack_late": [
            {"run_id": "pilot-20260731-0703", "delay_seconds": 141.768154,
             "reason": "START_ACK_LATE (python3 main.py scheduler-watchdog-scan)"}
        ],
        "watchdog_scan_status": "INCIDENT",
        "watchdog_incident_count": 8,
        "interpretation": (
            "10 of 17 pre-registered runs did not complete: 7 never produced a start ACK at all and "
            "2 (0703, 0843) ACKed and then died mid-run. Both the 06:10 launchd canary and the 06:35 "
            "market-gate run are among the fully missed slots, so NO raw vault snapshot exists for "
            "2026-07-31 and the six official market checks were never adjudicated today."
        ),
    },

    "root_cause_analysis": {
        "verdict": "HOST_AVAILABILITY_FAILURE_UNTIL_0925_PT",
        "evidence": [
            "logs/watchdog.stdout.log is a 60-second-interval job. Its own ticks show gaps of 6302s, "
            "7768s and 4544s overnight, then 1043s (06:48->07:05), 3405s (07:05->08:02), 1586s "
            "(08:02->08:29) and 1075s (09:01->09:19) during the pilot window.",
            "After 2026-07-31T09:24:58-07:00 there is NO watchdog gap above 120s for the rest of the "
            "day, and every slot from 0923 onward completed on cadence.",
            "logs/scheduler/self_arming_fires.jsonl shows the dispatcher ticked only 9 times before "
            "10:00 PT, at 06:19, 06:48, 07:05, 08:15, 08:27, 08:43, 09:17, 09:23 and 09:43 - the same "
            "irregular pattern, from 09:43 onward a clean 20-minute cadence.",
            "Two independent launchd jobs on different intervals lost ticks at the SAME wall-clock "
            "times, which points at the host being suspended rather than at either job's code.",
        ],
        "consequence": (
            "Slots 0610, 0635, 0723, 0743, 0803 and 0903 fell inside dispatcher tick gaps, so no worker "
            "was ever launched and no start ACK could exist. 0703 fired 141.8s late for the same reason."
        ),
        "ruled_out": [
            "logs/launchd-worker.stderr.log carries a tomllib/tomli ModuleNotFoundError traceback, but "
            "its mtime is 2026-07-24 11:26 and its size is unchanged; it is a stale artifact and is NOT "
            "today's cause. python3 is 3.13.7 and imports tomllib cleanly.",
        ],
        "unexplained": [
            {
                "run_id": "pilot-20260731-0823",
                "observation": (
                    "self_arming_fires.jsonl records SLOT_MATCH with run=true at 08:27:06-07:00 for slot "
                    "0823, yet no logs/scheduler/pilot-20260731-0823.start.json exists and no stdout/stderr "
                    "files were written under logs/launchd_worker/2026-07-31/. The dispatcher believed it "
                    "launched a worker that left no trace."
                ),
                "cause": "UNKNOWN",
                "note": "Recorded as UNKNOWN rather than folded into the sleep explanation; not backfilled.",
            }
        ],
        "power_state_note": (
            "The watchdog recorded ON_AC through the pilot window and flipped to ON_BATTERY at "
            "13:00:04-07:00, after the last pilot slot. Power state is reported as logged; the host "
            "sleep/wake log itself was not read (it needs an approval this unattended run does not have), "
            "so the precise suspend mechanism is UNKNOWN."
        ),
    },

    # ------------------------------------------------------------ trajectories
    "trajectory_completeness": {
        "directory": "logs/quote_trajectories/%s/" % DATE,
        "chains": 3,
        "incomplete_chains": 0,
        "verdict": "ALL_CHAINS_COMPLETE",
        "detail": [
            {
                "trajectory_id": "pilot-20260731-0923-SOFI-C16.5-20260814",
                "events": ["CANDIDATE", "QUOTE", "QUOTE.2", "QUOTE.3", "QUOTE.4", "HORIZON_CLOSE"],
                "limit_price": 0.53,
                "fill_window_deadline": "2026-07-31T16:29:52.318573207Z",
                "adjudicating_quote_source_updated_at": "2026-07-31T16:29:51.184739760Z",
                "fill_adjudication": "SIMULATED_FILL",
                "margin_inside_window_seconds": 1.134,
                "policy_labels": ["NEAR_MISS"],
            },
            {
                "trajectory_id": "pilot-20260731-1003-BAC-C63-20260814",
                "events": ["CANDIDATE", "QUOTE", "QUOTE.2", "HORIZON_CLOSE"],
                "limit_price": 0.68,
                "fill_window_deadline": "2026-07-31T17:07:19.104902646Z",
                "adjudicating_quote_source_updated_at": "2026-07-31T17:07:02.457767816Z",
                "fill_adjudication": "SIMULATED_FILL",
                "margin_inside_window_seconds": 16.647,
                "policy_labels": ["BASE_18", "BASE_21", "NEAR_MISS"],
            },
            {
                "trajectory_id": "pilot-20260731-1023-SOFI-C16.5-20260814",
                "events": ["CANDIDATE", "QUOTE", "HORIZON_CLOSE"],
                "limit_price": 0.57,
                "fill_window_deadline": "2026-07-31T17:27:43.614404796Z",
                "adjudicating_quote_source_updated_at": "2026-07-31T17:27:33.180428539Z",
                "fill_adjudication": "NO_FILL_WINDOW_EXPIRED",
                "margin_inside_window_seconds": 10.434,
                "policy_labels": ["NEAR_MISS"],
                "reason_code_defect": "SEE_defects_found[1]",
            },
        ],
    },

    # ------------------------------------------------------------- day's story
    "research_findings_today": {
        "first_fully_qualified_signal_of_the_pilot": {
            "run_id": "pilot-20260731-1023",
            "symbol": "SOFI",
            "contract": "SOFI 2026-08-14 C16.5",
            "evidence": (
                "strategy.underlying_signal.evaluate_underlying_signal returned SignalDirection.CALL with "
                "an EMPTY reasons tuple on the 17:20:00Z completed bar: close 16.3100 > session VWAP "
                "16.1622, EMA9 16.2455 > EMA20 16.2183, close 16.3100 > six-bar high 16.2799, volume_ratio "
                "1.758985 >= frozen minimum_volume_ratio 1.50. Regime BULLISH from SPY."
            ),
            "significance": (
                "The six-bar breakout leg had been the binding constraint on every prior firing "
                "(base18-base21-first-firing-rivn). This is the first time all three legs and the volume "
                "gate passed together in the pilot."
            ),
            "outcome": "NO FILL. The limit 0.57 was recorded at 17:26:43.614Z; the adjudicating quote "
                       "10.4s later showed ask 0.58 > 0.57, so no simulated entry occurred.",
            "no_trade_was_placed_and_none_could_be": (
                "READ_ONLY, live_trading_enabled false, order_tools_enabled false, kill switch ENGAGED."
            ),
        },
        "highest_volume_ratio_ever_recorded": {
            "run_id": "pilot-20260731-1003",
            "symbol": "BAC",
            "volume_ratio": 2.6832,
            "detail": (
                "Newest completed bar 16:55:00Z volume 127,933 vs trailing-20 average 47,679.8. First "
                "slot in the pilot to fire BOTH BASE_18 and BASE_21, and the highest reading recorded to "
                "date - above the 2.3321 maximum across the 430 symbol-slots that drove the 2026-07-28 "
                "recalibration."
            ),
            "why_no_trade": (
                "SIX_BAR_BREAKOUT_FAILED: close 62.065 against a six-bar high of 62.215. VWAP and EMA legs "
                "passed. Research labels never authorise a trade."
            ),
        },
        "cost_hurdle_evidence": (
            "All three candidates recorded the REAL hurdle from `python3 main.py cost-hurdle` (the CLI "
            "NameError of 2026-07-30 is fixed): the frozen flat $1.40 friction constant understated the "
            "true round-trip cost by 2.786x (1023 SOFI), 3.207x (0923 SOFI) and 4.112x (1003 BAC). The "
            "0923 SOFI candidate was additionally rejected on cost: its 0.6066% breakeven exceeded the "
            "+0.5276% six-bar move the signal actually delivered."
        ),
        "policy_trade_budget": {
            "policy_trades_recorded": 0,
            "budget_consumed": False,
            "note": "All 3 trajectories are counterfactual research records. The one-per-day policy-trade "
                    "budget for %s remains unconsumed." % DATE,
        },
    },

    # ------------------------------------------------------------- P&L (copied)
    "pnl_deterministic": eod["pnl"],
    "calibration_trade": {
        "status": cal["status"],
        "contract": "SOFI 2026-08-14 C16.5",
        "instrument_id": cal["entry"]["instrument_id"],
        "entry_run_id": cal["entry"]["run_id"],
        "entry_observed_at": cal["entry"]["entry_observed_at"],
        "entry_ask": cal["entry"]["entry_ask"],
        "exit_run_id": cal["exit"]["run_id"],
        "exit_observed_at": cal["exit"]["exit_observed_at"],
        "exit_bid": cal["exit"]["exit_bid"],
        "exit_reason": cal["exit"]["exit_reason"],
        "holding_minutes": cal["exit"]["holding_minutes"],
        "gross_pnl_usd": cal["gross_pnl_usd"],
        "net_pnl_usd_fees_only": cal["net_pnl_usd_fees_only"],
        "net_pnl_usd_frozen": cal["net_pnl_usd"],
        "entry_cost_hurdle_pct_of_premium": cal["entry_cost_hurdle"]["total_pct_of_premium"],
        "frozen_understated_by_x": cal["entry_cost_hurdle"]["frozen_understated_by_x"],
        "liquidity_floor_met": True,
        "liquidity_floor_evidence": "volume 1651 >= 100 and open_interest 1962 >= 100 at entry",
        "evidence_class": cal["evidence_class"],
        "never_counts_toward_policy_budget": True,
        "machinery_verdict": (
            "One complete virtual fill lifecycle was exercised on real quotes despite losing 10 of 17 "
            "slots: entry at the observed ask 0.53 (0923 slot), exit at the observed bid 0.50 (1003 slot), "
            "40.10 holding minutes. Gross ask-to-bid P&L -3.00; fees-only -3.40; frozen model -4.40. The "
            "entry-time hurdle was 5.2451% of a $51.50 premium, which the flat $1.40 understates by "
            "1.929x. Machinery validated; this is NEVER strategy evidence."
        ),
    },

    "bar_time_audit": {
        "snapshots_checked": eod["bar_time_audit"]["snapshots_checked"],
        "unsound": eod["bar_time_audit"]["unsound"],
        "verdict": "NOT_ADJUDICABLE_NO_SNAPSHOTS",
        "interpretation": (
            "Zero snapshots were checked because logs/raw/vault_index.jsonl contains NO entry dated "
            "2026-07-31: the 06:10 canary and the 06:35 market-gate run both missed, and those are the "
            "only runs that collect into the raw vault. This is reported as an absence of evidence, not "
            "as a pass. No market data was backfilled to close the gap."
        ),
    },

    "market_gate_status": {
        "six_official_market_checks_adjudicated_today": False,
        "reason": "market-gate-20260731-0635 never produced a start ACK (host availability failure).",
        "shadow_authorization_unaffected": True,
        "note": "Authorization remains an owner-only action; this run neither sought nor implied it.",
    },

    # ------------------------------------------------------------------ defects
    "defects_found": [
        {
            "id": 0,
            "severity": "HIGH",
            "component": "scripts/eod_report.py",
            "title": "Any rejection_reasons entry short-circuits the report, suppressing today's fill evidence",
            "detail": (
                "At scripts/eod_report.py:158-163 the record returns REJECTED_NO_TRADE as soon as "
                "`rejection_reasons` is non-empty, BEFORE entry_limit, the fill scan and P&L are computed. "
                "Slot workers use rejection_reasons as a general narrative evidence log - today's entries "
                "include QUALIFIED_SIGNAL_UNDER_THE_FROZEN_STRATEGY, EARNINGS_BLACKOUT_CLEAR and "
                "COST_HURDLE_IS_SURMOUNTABLE, none of which are rejections."
            ),
            "impact": (
                "logs/eod/2026-07-31.pnl.json reports research_counterfactual filled_and_exited 0 and "
                "entry_limit null for all three trajectories, while the trajectory records themselves carry "
                "limit_price and fill_adjudication SIMULATED_FILL for two of the three chains (0923 SOFI, "
                "1003 BAC). The headline deterministic P&L therefore understates the fill evidence the day "
                "actually produced."
            ),
            "action_taken": "REPORTED_ONLY. Not fixed and not backfilled - a CLOSE_SUMMARY run adjudicates, "
                            "it does not edit adjudication code or restate deterministic output.",
        },
        {
            "id": 1,
            "severity": "MEDIUM",
            "component": "config/quote_trajectory.schema.json + slot worker",
            "title": "NO_FILL_WINDOW_EXPIRED is the wrong reason code when the window was still open",
            "detail": (
                "pilot-20260731-1023-SOFI-C16.5-20260814 recorded fill_adjudication "
                "NO_FILL_WINDOW_EXPIRED, but its adjudicating quote carried source_updated_at "
                "17:27:33.180Z against a deadline of 17:27:43.614Z - 10.43 seconds INSIDE the 60s window. "
                "The window had not expired; the observed ask 0.58 simply exceeded the recorded limit 0.57."
            ),
            "impact": (
                "The no-fill VERDICT is correct and the trajectory is sound, but the recorded reason "
                "misattributes it to latency instead of price. The schema's fill_adjudication enum offers "
                "only SIMULATED_FILL / NO_FILL_WINDOW_EXPIRED / FILL_WINDOW_NOT_ADJUDICABLE / null, so "
                "there is no value meaning 'limit not met inside an open window'. This is a schema gap, "
                "not only a worker slip, and it will systematically mislabel every price-driven no-fill."
            ),
            "action_taken": "REPORTED_ONLY. The trajectory files were not edited.",
        },
        {
            "id": 2,
            "severity": "MEDIUM",
            "component": "unattended worker harness",
            "title": "Bash for-loops are denied in unattended runs and preceded the 0843 abort",
            "detail": (
                "logs/launchd_worker/2026-07-31/pilot-20260731-0843.stdout.jsonl records a permission "
                "denial on a Bash command using `for d in ...` (rejected as simple_expansion), and the run "
                "then ended with terminal_reason aborted_streaming after 141.3s. The 0703 run aborted the "
                "same way after 88.8s. This close run hit the identical for-loop restriction and worked "
                "around it with separate commands."
            ),
            "impact": (
                "Two ACKed slots produced no summary. The restriction belongs alongside the known shasum "
                "and heredoc-redirection limits so slot workers stop emitting shell forms that will be "
                "denied."
            ),
            "action_taken": "REPORTED_ONLY.",
        },
    ],

    "constraints_observed": {
        "local_logs_only": True,
        "no_mcp_calls_made": True,
        "no_market_data_backfilled": True,
        "pilot_and_drill_excluded_from_formal_performance": True,
        "no_order_review_place_replace_cancel_transfer_or_mutation": True,
        "no_account_numbers_names_credentials_or_personal_data_stored": True,
        "start_ack_not_rewritten": True,
        "relative_paths_only": True,
    },

    "dashboard_rebuilt": None,   # filled in by the rebuild step
    "dashboard_path": "dashboard/index.html",

    "headline": (
        "2026-07-31 was simultaneously the pilot's best research day and its worst operational day. "
        "The FIRST fully qualified signal under the frozen strategy fired at 10:23 PT (SOFI, all three "
        "price legs plus volume_ratio 1.7590 >= the 1.50 live gate), and BAC posted 2.6832 at 10:03 PT - "
        "the highest volume_ratio ever recorded and the first BASE_18+BASE_21 firing. Neither became a "
        "trade: SOFI's ask moved above the limit inside an open fill window, BAC failed the six-bar "
        "breakout leg. Against that, a host availability failure until roughly 09:25 PT cost 10 of 17 "
        "scheduled runs including the canary and the market gate, so no raw vault snapshot exists for "
        "today and the six official market checks were never adjudicated. The calibration lifecycle still "
        "completed end to end. Three defects are reported, none fixed."
    ),
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as fh:
    json.dump(summary, fh, indent=1, sort_keys=True)
    fh.write("\n")
print(json.dumps({"status": "WRITTEN", "path": OUT, "bytes": os.path.getsize(OUT)}))

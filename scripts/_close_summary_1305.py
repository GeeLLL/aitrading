"""Write the CLOSE_SUMMARY terminal receipt for pilot-close-canary-20260729-1305.

Local logs only. Every P&L / coverage / friction figure is copied verbatim from
logs/eod/2026-07-29.pnl.json (produced by scripts/eod_report.py); none is computed here.
"""
import json
import os
import subprocess

GEN = subprocess.run(
    ["date", "-u", "+%Y-%m-%dT%H:%M:%S+00:00"], capture_output=True, text=True
).stdout.strip()

eod = json.load(open("logs/eod/2026-07-29.pnl.json"))
sc = eod["slot_coverage"]
cal = eod["calibration_trade"]
rcost = cal["realistic_cost"]

summary = {
    "schema_version": 1,
    "run_id": "pilot-close-canary-20260729-1305",
    "kind": "CLOSE_SUMMARY",
    "scheduled_for": "2026-07-29T13:05:00-07:00",
    "observation_date": "2026-07-29",
    "generated_at": GEN,
    "status": "COMPLETED",
    "result": "SUCCESS_CLEAN_SCHEDULE_NO_TRADE_DAY",
    "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
    "data_sources": "LOCAL_LOGS_ONLY",
    "market_data_backfilled": False,
    "mcp_tools_used": [],
    "mutating_tools_used": [],
    "formal_performance": (
        "EXCLUDED. Every Pilot/Drill artifact on 2026-07-29 is PILOT_EXCLUDED_FROM_PERFORMANCE and the "
        "calibration trade is CALIBRATION_EXCLUDED_FROM_PERFORMANCE. Neither may ever be aggregated into "
        "a formal strategy result."
    ),
    "dashboard_rebuilt": True,
    "dashboard_path": "dashboard/index.html",

    "safety_gate": {
        "passed": True,
        "source": "python3 main.py status, run as the first action of this run, before reading any log",
        "system_mode": "READ_ONLY",
        "live_trading_enabled": False,
        "order_tools_enabled": False,
        "kill_switch_engaged": True,
        "kill_switch_reason": "TRADING_ARM_MARKER_ABSENT",
        "automation_halted": False,
        "approved_trade_stage": 1,
        "max_deployable_capital_usd": 300,
        "phase3_blockers": [],
    },

    "deterministic_adjudication": {
        "command": "python3 scripts/eod_report.py --date 2026-07-29",
        "machine_readable": "logs/eod/2026-07-29.pnl.json",
        "human_readable": "logs/eod/2026-07-29.report.md",
        "note": (
            "All headline P&L, slot-coverage and calibration figures in this summary are copied verbatim "
            "from that deterministic local run. No figure in this file was computed by the agent."
        ),
    },

    "schedule_reconciliation": {
        "expected_slots": sc["expected"],
        "completed": sc["completed"],
        "failed": sc["failed"],
        "missed": sc["missed"],
        "missing_schedules": [],
        "missing_schedules_note": (
            "ZERO missed slots on 2026-07-29. All 16 slots from launchd-canary-0610 through pilot-1123 carry "
            "a start ACK and a terminal receipt. This is a clean recovery from 2026-07-28, when pilot-1103 "
            "and pilot-1123 never launched and two market samples were permanently lost."
        ),
        "failed_slot_explanation": (
            "The single 'failed: 1' entry is THIS run, pilot-close-canary-20260729-1305, whose status was null "
            "at the moment scripts/eod_report.py read the receipt directory because its terminal summary is "
            "written after the report. It is an artifact of ordering, not a failure. All 16 prior slots are "
            "COMPLETED."
        ),
        "receipt_convention_note": (
            "launchd-canary-20260729-0610 uses the canary receipt convention "
            "(launchd-canary-20260729-0610.json, COMPLETED, 156.7s, SPY snapshot "
            "97394253-1354-4075-ab25-5e99204551f3, sha256 "
            "b790046d7ffecea9b115ee9da600341e23dd0d3bb96074a5d5d46ff94a4b4253) and has no .summary.json. "
            "The 15 .summary.json files are market-gate-0635 plus the 14 PILOT_SAMPLE slots 0703-1123."
        ),
        "watchdog_scan": {
            "command": "python3 main.py scheduler-watchdog-scan",
            "status": "INCIDENT",
            "expectations_checked": 34,
            "incidents": 2,
            "pending": [],
            "new_entries_blocked": True,
            "incidents_are_from_2026_07_28_only": True,
            "incident_detail": (
                "Both open scheduler incidents are yesterday's SCHEDULED_RUN_MISSED entries "
                "(2026-07-28T18:03:00Z and 2026-07-28T18:23:00Z). NO incident was raised for any 2026-07-29 "
                "slot. The scan audits 34 pre-registered expectations spanning more than one date, not just "
                "today's 17."
            ),
            "incident_files": [
                "logs/incidents/pilot-20260728-1103.scheduler-incident.json",
                "logs/incidents/pilot-20260728-1123.scheduler-incident.json",
            ],
        },
        "other_open_incident": {
            "file": "logs/incidents/power-on_battery-2026-07-29.scheduler-incident.json",
            "incident_type": "COLLECTION_WINDOW_POWER_RISK",
            "severity": "WARNING",
            "reason": "ON_BATTERY",
            "detected_at": "2026-07-29T15:32:46.950377+00:00",
            "requires_owner_review": True,
            "assessment": (
                "Raised at 08:32 local while the Mac was off AC during the collection window. It did NOT cost "
                "a slot today - every subsequent slot 0843 through 1123 fired on time - but it is the same "
                "class of risk that silently cost two slots on 2026-07-28. Left open for the owner."
            ),
        },
    },

    "deterministic_slot_coverage_verbatim": sc,

    "market_gate": {
        "run_id": "market-gate-20260729-0635",
        "status": "COMPLETED_WITH_FAILED_CHECK",
        "gate_verdict": "NOT_QUALIFIED",
        "checks_passed": 5,
        "checks_failed": 1,
        "checks_unknown": 0,
        "results": {
            "official_raw_mcp_snapshot": "PASS",
            "raw_to_feature_reproducibility": "PASS",
            "fresh_option_quote": "PASS",
            "official_account_cash_reconciliation": "PASS",
            "official_orders_positions_reconciliation": "PASS",
            "official_instrument_session": "FAIL",
        },
        "failure_reason": "TRADABILITY_PROBE_FAILED_CLOSED_NO_HARVESTED_SESSION_EVIDENCE",
        "root_cause": (
            "get_equity_tradability now requires an account_number argument; "
            "prompts/robinhood_tradability_probe.md supplies only the symbol and the probe agent is allowed "
            "no other tool, so the single permitted call always fails validation with "
            "'invalid params: required: missing properties: [account_number]'. Two attempts, both failed."
        ),
        "handling_note": (
            "Correct fail-closed behaviour: --session-snapshot was deliberately NOT passed to "
            "market-check-verify and no instrument_session object was hand-written as though harvested. "
            "The check is recorded FAIL with the real reason."
        ),
        "bar_time_verification": {
            "status": "FAIL",
            "reason": "BAR_TIME_IRREGULARITIES",
            "verdict_reported_as_returned": True,
            "detail": (
                "SPY and QQQ each carry 78 five-minute bars whose newest bar begins 2026-07-28T19:55:00Z, "
                "flagged BARS_FROM_PRIOR_SESSION. A pre-open snapshot legitimately carries prior-session "
                "bars; the verdict was recorded exactly as adjudicated and was not adjusted."
            ),
        },
    },

    "bar_time_audit_verbatim": eod["bar_time_audit"],

    "trajectories": {
        "files_written": 24,
        "distinct_trajectories": 21,
        "schema_conformance": (
            "PASS. All 24 files checked against config/quote_trajectory.schema.json for required keys, enum "
            "membership, const fields and the additionalProperties:false constraint - 0 nonconforming. NOTE: "
            "the python jsonschema package is NOT installed in this environment, so this was a manual "
            "structural check, not a full JSON-Schema validation; format and numeric-bound assertions were "
            "not exercised."
        ),
        "incomplete_trajectories": [],
        "incomplete_trajectories_note": (
            "NONE. Completeness rule applied: a trajectory is incomplete if it records a limit_price without "
            "a fill_adjudication. 3 of 21 trajectories recorded an entry limit and all 3 carry an explicit "
            "adjudication; the remaining 18 opened no fill window and correctly carry fill_adjudication null "
            "by construction, not by omission."
        ),
        "slots_with_no_candidate": ["pilot-20260729-0803", "pilot-20260729-0823"],
        "slots_with_no_candidate_note": (
            "Both slots COMPLETED successfully and legitimately recorded no candidate: all 13 universe "
            "symbols returned NO_TRADE on VOLUME_CONFIRMATION_FAILED with every volume_ratio below 1.0, and "
            "no symbol cleared all three structural legs, so no NEAR_MISS met the recording bar. Absence of "
            "a file here is a real observation, not a gap."
        ),
        "outcomes": {"REJECTED_NO_TRADE": 21},
        "policy_label_counts": {"BASE_18": 5, "BASE_21": 3, "NEAR_MISS": 19},
        "fill_adjudications": {
            "SIMULATED_FILL": 0,
            "NO_FILL_WINDOW_EXPIRED": 2,
            "FILL_WINDOW_NOT_ADJUDICABLE": 1,
            "null_no_window_opened": 18,
        },
    },

    "fill_window_audit": {
        "windows_opened": 3,
        "simulated_fills": 0,
        "detail": [
            {
                "trajectory": "pilot-20260729-0703-BAC-P62-20260814",
                "limit": 1.385,
                "limit_recorded_at": "2026-07-29T14:06:30.424941Z",
                "deadline": "2026-07-29T14:07:30.424941Z",
                "refresh_observed_at": "2026-07-29T14:07:44.186953Z",
                "elapsed_seconds_local_receipt_clock": 73.76,
                "elapsed_seconds_broker_venue_clock": 54.73,
                "inside_60s_window_on_receipt_clock": False,
                "inside_60s_window_on_venue_clock": True,
                "observed_ask": 1.45,
                "adjudication": "NO_FILL_WINDOW_EXPIRED",
                "note": (
                    "Immaterial to the outcome either way: the observed ask 1.45 was above the 1.385 limit, so "
                    "no fill would have been recorded on either clock."
                ),
            },
            {
                "trajectory": "pilot-20260729-0723-IWM-P290-20260812",
                "limit": 4.8175,
                "limit_recorded_at": "2026-07-29T14:25:04.416050Z",
                "deadline": "2026-07-29T14:26:04.416050Z",
                "refresh_observed_at": "2026-07-29T14:26:00.565207Z",
                "elapsed_seconds_local_receipt_clock": 56.15,
                "elapsed_seconds_broker_venue_clock": 47.54,
                "inside_60s_window_on_receipt_clock": True,
                "inside_60s_window_on_venue_clock": True,
                "observed_ask": 5.07,
                "adjudication": "NO_FILL_WINDOW_EXPIRED",
                "note": (
                    "The only window that was inside 60s on BOTH clocks. The observed ask 5.07 was above the "
                    "4.8175 limit, so this is the one unambiguous no-fill on market behaviour today."
                ),
            },
            {
                "trajectory": "pilot-20260729-0903-RIVN-P16-20260814",
                "limit": 1.03,
                "limit_recorded_at": "2026-07-29T16:05:55.548540Z",
                "deadline": "2026-07-29T16:06:55.548540Z",
                "refresh_observed_at": "2026-07-29T16:07:03.471520Z",
                "elapsed_seconds_local_receipt_clock": 67.92,
                "elapsed_seconds_broker_venue_clock": 43.99,
                "inside_60s_window_on_receipt_clock": False,
                "inside_60s_window_on_venue_clock": True,
                "observed_ask": 1.03,
                "adjudication": "FILL_WINDOW_NOT_ADJUDICABLE",
                "note": (
                    "MATERIAL, and the single most important observation of the day. The observed ask 1.03 "
                    "was exactly AT the 1.03 limit, and the broker's own source_updated_at stamp puts that "
                    "quote at +43.99s - comfortably INSIDE the 60s window. On the authoritative venue clock "
                    "this was a fill. It was recorded FILL_WINDOW_NOT_ADJUDICABLE only because the local "
                    "receipt clock, which includes model turn latency, read +67.92s. The adjudication is "
                    "left exactly as the slot recorded it and is NOT retroactively converted to a fill - but "
                    "the day's only fill observation was lost to a clock choice, not to latency and not to "
                    "market behaviour."
                ),
            },
        ],
        "finding": (
            "All 3 fill windows were INSIDE the 60s maximum_fill_wait_seconds on the broker's authoritative "
            "source_updated_at stamp (54.73s, 47.54s, 43.99s). On the local receipt clock 2 of 3 read as "
            "expired (73.76s and 67.92s). The divergence is 13-24s and is model turn latency between the "
            "MCP call returning and the next readable wall clock - time the harness cannot stamp. Adjudicating "
            "on receipt time therefore systematically biases outcomes toward NO_FILL and understates the fill "
            "rate; it cost the only fill observation of 2026-07-29."
        ),
        "recommended_fix": (
            "Adjudicate the window on source_updated_at, the broker-supplied stamp present on every option "
            "quote and the only timestamp both sides can verify, rather than on the local receipt reading. "
            "Cutting the instructed wait to ~35-40s is a partial mitigation that reduces but does not remove "
            "the bias, because the unmeasurable latency band remains."
        ),
    },

    "simulated_pnl_verbatim": eod["pnl"],

    "pnl_note": (
        "Zero policy trajectories and zero fills of 21 research counterfactual trajectories, so both policy "
        "and research net P&L are $0.00 - an absence of trades, not a breakeven result. No P&L figure here "
        "was computed by the agent."
    ),

    "policy_trade_budget_discrepancy": {
        "observed": (
            "The 07:03 slot's BAC record states 'POLICY_TRADE_BUDGET: ... The one-per-day budget is CONSUMED "
            "by this candidate', while the 10:23 and later slots state 'POLICY_TRADE_BUDGET_NOT_CONSUMED ... "
            "no policy trade has been recorded on 2026-07-29'."
        ),
        "adjudication": (
            "The durable record agrees with the later slots. scripts/eod_report.py classifies all 21 "
            "trajectories as research_counterfactual and policy trajectories as 0, because the BAC candidate "
            "failed CONTRACT_ELIGIBILITY (option volume 5 < 500; premium $137.00 > the $75 stage-1 cap) and "
            "so never became a policy entry."
        ),
        "materiality": (
            "Bookkeeping wording only - no trade, no fill and no P&L turned on it, and no later slot was "
            "blocked by the mis-stated budget. Flagged so the phrasing can be tightened, not because an "
            "entry was wrongly permitted."
        ),
    },

    "calibration_trade": {
        "status": cal["status"],
        "evidence_class": cal["evidence_class"],
        "contract": "SOFI put 15.0 exp 2026-08-14, 16 DTE, premium band $75",
        "entry": {
            "run_id": "pilot-20260729-0703",
            "observed_at": "2026-07-29T14:08:06.903783Z",
            "at_observed_ask": 0.71,
        },
        "exit": {
            "run_id": "pilot-20260729-0743",
            "observed_at": "2026-07-29T14:48:27.358315Z",
            "at_observed_bid": 0.65,
            "reason": "HORIZON_40_MIN",
            "holding_minutes": 40.3409,
        },
        "deterministic_pnl": {
            "gross_pnl_usd": cal["gross_pnl_usd"],
            "frozen_friction_usd": cal["friction_usd"],
            "net_pnl_usd": cal["net_pnl_usd"],
            "net_pnl_usd_realistic": cal["net_pnl_usd_realistic"],
            "realistic_cost_usd": rcost["total_cost_usd"],
            "realistic_cost_pct_of_premium": rcost["total_pct_of_premium"],
            "frozen_understated_by_x": rcost["frozen_understated_by_x"],
        },
        "liquidity_floor": (
            "PASSED - volume 468 >= 100 and open_interest 2749 >= 100. Today's friction datapoint is "
            "trustworthy, unlike the 2026-07-28 IWM contract which had volume 0 and OI 0."
        ),
        "machinery_verdict": (
            "The entry/exit/friction/P&L machinery completed one full virtual lifecycle on real quotes on a "
            "day with zero policy trades, which is exactly what the calibration trade exists to prove. It "
            "consumed no part of the one-per-day policy budget and is never strategy evidence."
        ),
    },

    "day_narrative": {
        "regime": (
            "BEARISH for the first eight slots (0703-0943), then a confirmed bullish reversal on both SPY and "
            "QQQ from the 10:23 slot onward. Every candidate through 10:03 was a put; every candidate from "
            "10:23 was a call except the final BAC put at 11:23."
        ),
        "first_qualified_signal": (
            "07:03 BAC: the FIRST qualified underlying signal of the entire pilot. All four frozen bearish "
            "legs passed at volume_ratio 2.7675 (>= the frozen 1.50 live gate), firing both research labels "
            "BASE_18 and BASE_21. NVDA also qualified at 1.9485. It could not become a policy entry: the "
            "selected BAC 62 put had option volume 5 (< the 500 minimum) and a $137.00 premium (> the $75 "
            "stage-1 cap)."
        ),
        "record_volume_ratio": (
            "09:23 BAC volume_ratio 4.4796 (518,640 versus trailing-20 average 115,777.6) - the highest ever "
            "recorded in this pilot, 1.92x the 2.3321 maximum of the 430-slot distribution that motivated "
            "the 2026-07-28 recalibration, and 1.57x the prior same-day record of 2.8475 set by RIVN at "
            "09:03. It failed SIX_BAR_BREAKDOWN by 33.0 cents. Two consecutive slots now show record volume "
            "failing on structure rather than on volume."
        ),
        "recalibration_verdict": (
            "The 2026-07-28 recalibration is vindicated. BASE_18 fired 5 times and BASE_21 fired 3 times on "
            "2026-07-29, against zero firings under the retired BASE_25/BASE_30 thresholds across the "
            "preceding 430 symbol-slots. The labels are now producing research signal instead of being "
            "structurally incapable of firing."
        ),
        "budget_universe_mismatch": (
            "The day's cleanest signal was unreachable. At 10:23 four symbols cleared the frozen 1.50 volume "
            "gate and three of them (MSFT 396.495, AMZN 230.755, META 594.250) cleared all three bullish "
            "structural legs - but their near-the-money 7-21 DTE calls cost $1057.50 to $1627.50 per "
            "contract, 14x to 22x the $75 stage-1 cap and 8x to 14x the $120 absolute cap. The symbols the "
            "cap can afford (SOFI 15.525, RIVN 16.430, BAC 61.975) did not clear the gate. This is now a "
            "repeated structural finding, not a one-off."
        ),
        "cost_hurdle_evidence": (
            "Every candidate recorded a real cost hurdle from python3 main.py cost-hurdle rather than the "
            "flat constant. The frozen flat-$1.40 friction_model was measured understating true round-trip "
            "cost by 2.472x on the SOFI calibration trade, 4.828x on the 09:23 BAC contract, 6.558x on the "
            "07:03 BAC contract, 42.64x on the AMZN call and 140.91x on the MSFT call. The understatement "
            "scales with premium and spread, so the flat constant is most misleading exactly on the "
            "expensive contracts the signal keeps selecting."
        ),
    },

    "missing_or_degraded_evidence": [
        "official_instrument_session FAIL at the 06:35 market gate - get_equity_tradability requires an "
        "account_number the probe cannot supply. The gate is NOT_QUALIFIED and cannot go green until this "
        "is fixed.",
        "bar-time verification FAIL (BARS_FROM_PRIOR_SESSION) on the pre-open gate snapshot - expected for a "
        "pre-open collection and reported as adjudicated, not adjusted.",
        "1 of 2 vaulted snapshots unsound in the deterministic bar-time audit, for the same pre-open reason.",
        "The RIVN 09:03 fill window was recorded FILL_WINDOW_NOT_ADJUDICABLE on the local receipt clock "
        "(+67.92s) even though the broker's source_updated_at puts the same quote at +43.99s, inside the 60s "
        "window, with the observed ask 1.03 exactly at the 1.03 limit. The adjudication was left as recorded "
        "and no fill was invented, but the day's only fill observation is lost to a clock choice.",
        "python jsonschema is not installed, so trajectory conformance was checked structurally rather than "
        "by full JSON-Schema validation.",
        "COLLECTION_WINDOW_POWER_RISK (ON_BATTERY) raised 08:32 local and still open.",
    ],

    "owner_actions_required": [
        "P0 - Fix prompts/robinhood_tradability_probe.md (or the probe's tool grant) so get_equity_tradability "
        "receives an account_number. This is the single blocker keeping the market gate at 5/6 and "
        "NOT_QUALIFIED.",
        "P0 - Adjudicate the 60s fill window on the broker's source_updated_at rather than on the local "
        "receipt clock. All 3 windows today were inside 60s on the venue stamp; 2 of 3 read as expired on "
        "receipt time, and that clock choice alone cost the only fill observation of the day (RIVN, ask 1.03 "
        "at the 1.03 limit, venue-stamped +43.99s). Receipt-time adjudication biases every future fill rate "
        "downward. Cutting the instructed wait to ~35-40s is a partial mitigation only.",
        "P1 - Resolve the budget/universe mismatch: the $75 stage-1 cap cannot buy a contract on any symbol "
        "whose signal currently qualifies. Either raise the stage-1 cap, add affordable underlyings to "
        "config/universe.toml, or accept that stage 1 will keep producing unreachable signals.",
        "P2 - Plug the Mac into AC during the 06:10-13:05 collection window; the ON_BATTERY incident is the "
        "same failure class that cost two slots on 2026-07-28.",
        "P2 - Review and clear the two open 2026-07-28 SCHEDULED_RUN_MISSED incidents; they still set "
        "new_entries_blocked=true as an advisory.",
        "P3 - Tighten the 07:03 slot's POLICY_TRADE_BUDGET wording so a candidate that fails contract "
        "eligibility is not described as consuming the one-per-day budget.",
    ],

    "constraint_compliance": {
        "local_logs_only": True,
        "mcp_calls_made": 0,
        "market_data_backfilled": False,
        "missing_schedules_reported": True,
        "incomplete_trajectories_reported": True,
        "pilot_and_drill_excluded_from_formal_performance": True,
        "no_account_identifiers_or_personal_data_stored": True,
        "mutating_tools_used": [],
        "start_ack_not_rewritten": True,
        "no_figure_self_computed": (
            "All P&L, coverage, friction and cost-hurdle numbers are copied from logs/eod/2026-07-29.pnl.json "
            "or from the durable per-slot records. Elapsed fill-window seconds are arithmetic on two recorded "
            "timestamps within the same trajectory file."
        ),
    },

    "close_note": (
        "2026-07-29 was a clean-schedule, zero-trade day: 16 of 16 scheduled slots fired with zero misses, "
        "the first qualified underlying signals of the pilot appeared (07:03 BAC bearish, 10:23 MSFT/AMZN/META "
        "bullish), the recalibrated BASE_18/BASE_21 labels fired 5 and 3 times against zero under the retired "
        "thresholds, and not one candidate could become a policy entry - blocked by option-volume floors and "
        "by the $75 stage-1 premium cap that no qualifying symbol's contract fits. The calibration trade "
        "completed a full virtual lifecycle on a liquid SOFI put and measured the frozen friction constant "
        "understating real cost by 2.472x. Nothing here is formal performance."
    ),
}

OUT = "logs/launchd_worker/2026-07-29/pilot-close-canary-20260729-1305.summary.json"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as fh:
    json.dump(summary, fh, indent=2, ensure_ascii=False)
print(json.dumps({"status": "WROTE", "path": OUT, "bytes": os.path.getsize(OUT)}))

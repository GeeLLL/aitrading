"""Write the CLOSE_SUMMARY terminal receipt for pilot-close-canary-20260730-1305.

Local logs only. No market data is fetched or backfilled. Every headline P&L,
slot-coverage and calibration figure is copied verbatim from the deterministic
local adjudication in logs/eod/2026-07-30.pnl.json.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
EOD = json.loads((ROOT / "logs/eod/2026-07-30.pnl.json").read_text())
OUT = ROOT / "logs/launchd_worker/2026-07-30/pilot-close-canary-20260730-1305.summary.json"

cal = EOD["calibration_trade"]

summary = {
    "schema_version": 1,
    "run_id": "pilot-close-canary-20260730-1305",
    "kind": "CLOSE_SUMMARY",
    "scheduled_for": "2026-07-30T13:05:00-07:00",
    "observation_date": "2026-07-30",
    "generated_at": "2026-07-30T13:08:00-07:00",
    "status": "COMPLETED",
    "result": "SUCCESS_CLEAN_SCHEDULE_NO_POLICY_TRADE_DAY",
    "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
    "data_sources": "LOCAL_LOGS_ONLY",
    "market_data_backfilled": False,
    "mcp_tools_used": [],
    "mutating_tools_used": [],
    "formal_performance": (
        "EXCLUDED. Every Pilot/Drill artifact on 2026-07-30 is "
        "PILOT_EXCLUDED_FROM_PERFORMANCE and the calibration trade is "
        "CALIBRATION_EXCLUDED_FROM_PERFORMANCE. Neither may ever be aggregated "
        "into a formal strategy result."
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
        "command": "python3 scripts/eod_report.py --date 2026-07-30",
        "machine_readable": "logs/eod/2026-07-30.pnl.json",
        "human_readable": "logs/eod/2026-07-30.report.md",
        "note": (
            "All headline P&L, slot-coverage, fill and calibration figures in this "
            "summary are copied verbatim from that deterministic local run. No "
            "figure in this file was computed by the agent."
        ),
    },

    "schedule_reconciliation": {
        "expected_slots": EOD["slot_coverage"]["expected"],
        "completed": EOD["slot_coverage"]["completed"],
        "failed": EOD["slot_coverage"]["failed"],
        "missed": EOD["slot_coverage"]["missed"],
        "missing_schedules": [],
        "missing_schedules_note": (
            "ZERO missed slots on 2026-07-30. All 17 pre-registered runs "
            "(launchd-canary-0610, market-gate-0635, the 14 PILOT_SAMPLE slots "
            "0703 through 1123, and this close canary) carry a start ACK in "
            "logs/scheduler/. This is the second consecutive clean schedule day "
            "after the 2026-07-28 plist incident."
        ),
        "failed_slot_explanation": (
            "The single 'failed: 1' entry is THIS run, "
            "pilot-close-canary-20260730-1305, whose status was null at the moment "
            "scripts/eod_report.py read the receipt directory because its terminal "
            "summary is written after the report. It is an artifact of ordering, "
            "not a failure. All 16 prior slots are COMPLETED."
        ),
        "receipt_convention_note": (
            "launchd-canary-20260730-0610 uses the canary receipt convention "
            "(launchd-canary-20260730-0610.json, COMPLETED, 133.25s, SPY snapshot "
            "55b58c6b-0ec5-489e-b219-cd006a487f64, sha256 "
            "9cc36178a1c8472d31c034a02c0f8e224402fd19757e5542630d3c986fb0c4a5) and "
            "has no .summary.json. The 15 .summary.json files present before this "
            "run are market-gate-0635 plus the 14 PILOT_SAMPLE slots 0703-1123."
        ),
        "slot_statuses": {
            "SUCCESS": [
                "market-gate-20260730-0635", "pilot-20260730-0803",
                "pilot-20260730-0823", "pilot-20260730-0843",
                "pilot-20260730-0903", "pilot-20260730-0923",
                "pilot-20260730-1003", "pilot-20260730-1023",
                "pilot-20260730-1043", "pilot-20260730-1103",
                "pilot-20260730-1123",
            ],
            "SUCCESS_WITH_DEVIATIONS": [
                "pilot-20260730-0703", "pilot-20260730-0723", "pilot-20260730-0743",
            ],
            "note": (
                "No slot reported FAILURE. The three SUCCESS_WITH_DEVIATIONS slots "
                "each recorded their deviation explicitly in their own summary; the "
                "deviations are carried forward under open_defects below."
            ),
        },
    },

    "watchdog_scan": {
        "command": "python3 main.py scheduler-watchdog-scan",
        "status": "INCIDENT",
        "expectations_checked": 51,
        "incidents": 2,
        "pending": [],
        "new_entries_blocked": True,
        "incidents_are_from_2026_07_28_only": True,
        "incident_detail": (
            "Both open scheduler incidents remain yesterday-but-one's "
            "SCHEDULED_RUN_MISSED entries (2026-07-28T18:03:00Z and "
            "2026-07-28T18:23:00Z), still unacknowledged. NO incident was raised for "
            "any 2026-07-29 or 2026-07-30 slot. The scan now audits 51 "
            "pre-registered expectations spanning three dates, not just today's 17."
        ),
        "new_entries_blocked_note": (
            "'new_entries_blocked' is a derived advisory flag (main.py:644 sets it "
            "to bool(incidents)); it is not an enforcement mechanism and did not "
            "prevent today's 17 expectations from registering or running. It stays "
            "true until an owner acknowledges the two 2026-07-28 incidents."
        ),
        "owner_action_required": (
            "Acknowledge or formally write off the two 2026-07-28 missed slots so "
            "the scan can return to HEALTHY. Read-only worker cannot acknowledge."
        ),
    },

    "market_gate_2026_07_30": {
        "run_id": "market-gate-20260730-0635",
        "six_official_checks": "ALL_PASS",
        "checks": {
            "official_raw_mcp_snapshot": "PASS",
            "raw_to_feature_reproducibility": "PASS",
            "official_account_cash_reconciliation": "PASS",
            "official_orders_positions_reconciliation": "PASS",
            "official_instrument_session": "PASS",
            "fresh_option_quote": "PASS",
        },
        "artifacts": [
            "logs/qualification/2026-07-30/market-gate-20260730-0635.market-checks.json",
            "logs/qualification/2026-07-30/market-gate-20260730-0635.evidence.json",
            "logs/qualification/2026-07-30/market-gate-20260730-0635.bar-times.json",
        ],
        "shadow_authorization": "NOT_AUTHORIZED. No run today authorized formal Shadow; authorization is an owner-only action and its tools are denied to this worker.",
    },

    "bar_time_audit": {
        "snapshots_checked": EOD["bar_time_audit"]["snapshots_checked"],
        "unsound": EOD["bar_time_audit"]["unsound"],
        "provenance": EOD["bar_time_audit"]["provenance"],
        "detail": EOD["bar_time_audit"]["detail"],
        "interpretation": (
            "The one unsound snapshot is the 06:35 PT market-gate snapshot "
            "a53e4b20-adde-428d-9a2f-2aa4f7961fdb, which carries BARS_FROM_PRIOR_SESSION "
            "(newest bar 2026-07-29T19:55Z, lag 63492.978s). That is the legitimate and "
            "expected shape of a pre-open snapshot and the verdict is reported exactly as "
            "the deterministic verifier returned it, never adjusted. The 06:10 canary "
            "snapshot passed with freshness not enforced."
        ),
    },

    "trading_activity": {
        "policy_trades": 0,
        "policy_trade_budget_consumed": False,
        "policy_trade_budget_note": (
            "The one-per-day policy-trade budget for 2026-07-30 was never consumed. No "
            "universe symbol produced a qualifying signal in any of the 14 slots."
        ),
        "research_counterfactual_trajectories": EOD["pnl"]["research_counterfactual"]["trajectories"],
        "orders_placed": 0,
        "order_tools_touched": False,
    },

    "signal_outcome": {
        "universe_source": "config/universe.toml",
        "symbol_count": 13,
        "all_symbols_evaluated_every_slot": True,
        "research_labels_fired": {"BASE_18": 0, "BASE_21": 0},
        "research_label_note": (
            "No slot on 2026-07-30 fired BASE_18 (1.8) or BASE_21 (2.1). The highest "
            "volume_ratio recorded in any slot was 1.5096 (AMD, 11:23), well below both "
            "recalibrated research thresholds."
        ),
        "frozen_live_gate_crossings": (
            "Two symbols cleared the frozen 1.50 minimum_volume_ratio in the 11:23 slot - "
            "AMD 1.5096 and RIVN 1.5051 - and BOTH were then rejected by the same "
            "structural leg, SIX_BAR_BREAKOUT_FAILED. This is the second consecutive day "
            "on which every rejection at or above the volume gate came from the six-bar "
            "structure test rather than from volume."
        ),
        "day_decision": "UNIVERSE_WIDE_NO_TRADE across all 14 pilot slots.",
    },

    "trajectory_completeness": {
        "directory": "logs/quote_trajectories/2026-07-30/",
        "chains": 4,
        "files": 14,
        "incomplete_trajectories": [],
        "incomplete_note": (
            "ZERO incomplete trajectories. All four chains reached a terminal "
            "HORIZON_CLOSE event inside the trading day, so nothing is left open "
            "overnight and no cross-day backfill is needed or attempted."
        ),
        "chains_detail": [
            {
                "trajectory_id": "pilot-20260730-0723-IWM-P290-20260813",
                "events": ["CANDIDATE", "QUOTE", "QUOTE.2", "HORIZON_CLOSE"],
                "fill_adjudication": "SIMULATED_FILL",
                "limit_price": 4.62,
                "in_window_observed_ask": 4.52,
                "horizon_close_bid": 4.51,
                "note": "Fill refresh landed +20.4s after the limit, inside the frozen 60s window.",
            },
            {
                "trajectory_id": "pilot-20260730-0943-RIVN-C16.5-20260814",
                "events": ["CANDIDATE", "QUOTE", "QUOTE.3", "HORIZON_CLOSE"],
                "fill_adjudication": "SIMULATED_FILL",
                "limit_price": 1.03,
                "in_window_observed_ask": 1.03,
                "horizon_close_bid": 0.99,
                "note": (
                    "Fill refresh landed +22s, inside the window, BUT source_updated_at was "
                    "byte-identical on both observations, so the venue quote never advanced "
                    "between them. The trajectory records this stale-tick caveat itself; the "
                    "fill is a re-observation of one tick, not two independent ticks."
                ),
            },
            {
                "trajectory_id": "pilot-20260730-1043-TSLA-C307.5-20260814",
                "events": ["CANDIDATE", "QUOTE", "HORIZON_CLOSE"],
                "fill_adjudication": "NO_FILL_WINDOW_EXPIRED",
                "limit_price": 12.2,
                "in_window_observed_ask": 12.3,
                "note": (
                    "Refresh landed +50.9s, 9.7s inside the deadline, but the ask moved "
                    "AGAINST the limit (12.20 -> 12.30), so no fill. Closed early at the "
                    "11:23 last pilot slot, 75.5s before the true horizon instant."
                ),
            },
            {
                "trajectory_id": "pilot-20260730-1103-RIVN-C16.5-20260814",
                "events": ["CANDIDATE", "QUOTE", "HORIZON_CLOSE"],
                "fill_adjudication": "NO_FILL_WINDOW_EXPIRED",
                "limit_price": 1.15,
                "in_window_observed_ask": 1.15,
                "note": (
                    "The observed ask exactly MET the limit but arrived 68.97s after the limit "
                    "on the venue clock - 8.97s past the 60s deadline - because the limit quote "
                    "itself carried a 21.86s-old venue tick. Correctly recorded as no fill. "
                    "FORCED_CLOSE_LAST_PILOT_SLOT at 20.8 of 40 horizon minutes."
                ),
            },
        ],
        "eod_adjudication_of_trajectories": (
            "The deterministic close-of-day adjudicator classified all four chains as "
            "REJECTED_NO_TRADE and booked NO P&L for any of them: filled_and_exited 0, "
            "gross_pnl_usd 0.0, net_pnl_usd 0.0 across 4 research_counterfactual "
            "trajectories. The two SIMULATED_FILL adjudications live inside rejected "
            "counterfactual candidates, so they exercise the fill machinery without "
            "producing a P&L figure. That is the intended behaviour, not a discrepancy."
        ),
    },

    "calibration_trade": {
        "status": cal["status"],
        "evidence_class": cal["evidence_class"],
        "never_counts_toward_policy_budget": True,
        "symbol": cal["entry"]["symbol"],
        "contract": "SOFI 2026-08-14 C16.0",
        "entry_run_id": cal["entry"]["run_id"],
        "entry_observed_at": cal["entry"]["entry_observed_at"],
        "entry_ask": cal["entry"]["entry_ask"],
        "exit_run_id": cal["exit"]["run_id"],
        "exit_observed_at": cal["exit"]["exit_observed_at"],
        "exit_bid": cal["exit"]["exit_bid"],
        "exit_reason": cal["exit"]["exit_reason"],
        "holding_minutes": cal["exit"]["holding_minutes"],
        "liquidity_floor_met": True,
        "liquidity_floor_evidence": "volume 260 >= 100 and open_interest 1511 >= 100 at entry",
        "gross_pnl_usd": cal["gross_pnl_usd"],
        "net_pnl_usd_frozen": cal["net_pnl_usd"],
        "net_pnl_usd_fees_only": cal["net_pnl_usd_fees_only"],
        "fees_usd": cal["fees_usd"],
        "frozen_friction_usd": cal["friction_usd"],
        "entry_cost_hurdle": cal["entry_cost_hurdle"],
        "machinery_verdict": (
            "One complete virtual fill lifecycle was exercised on real quotes: entry at the "
            "observed ask 0.60 (07:03 slot), exit at the observed bid 0.60 (07:43 slot), "
            "40.21 holding minutes. Gross ask-to-bid P&L is exactly 0.00; the frozen model "
            "books -1.40 and a fees-only view books -0.40. The entry-time hurdle was 5.9551% "
            "of a $58.00 premium ($3.00 spread + $0.05 decay + $0.40 fees), which the frozen "
            "flat $1.40 constant understates by 2.467x. The machinery is validated; this is "
            "NEVER strategy evidence."
        ),
    },

    "pnl_formal": {
        "included_in_formal_performance": False,
        "policy": EOD["pnl"]["policy"],
        "research_counterfactual": EOD["pnl"]["research_counterfactual"],
        "friction_model": EOD["pnl"]["friction_model"],
        "round_trip_friction_usd": EOD["pnl"]["round_trip_friction_usd"],
        "note": (
            "Zero policy trajectories and zero booked counterfactual fills, so the day "
            "contributes no P&L in either bucket. The only realised virtual lifecycle is "
            "the calibration trade, which is excluded from performance by class."
        ),
    },

    "open_defects": [
        {
            "id": "COST_HURDLE_CLI_BROKEN",
            "severity": "P0",
            "first_observed": "2026-07-30 07:03 slot",
            "slots_affected": ["0703", "0723", "0943", "1043", "1103"],
            "detail": (
                "`python3 main.py cost-hurdle` raises NameError: cost_hurdle_command is not "
                "defined. main.py dispatches to a handler that was never written, so the "
                "argparse subparser is unreachable. The subcommand has never been executable "
                "since it was added in c6293f4."
            ),
            "handling": (
                "Affected slots did NOT hand-compute the hurdle. They drove "
                "research.cost_model.round_trip_cost directly with the same "
                "config/safety.toml [friction_model], which is the code path the CLI would "
                "have used. Figures are therefore still machine-produced."
            ),
            "status": "OPEN - the run instructions still prescribe a command that cannot run.",
        },
        {
            "id": "FILL_WINDOW_AIMED_OFF_LOCAL_CLOCK",
            "severity": "P1",
            "slots_affected": ["1103"],
            "detail": (
                "The 11:03 refresh missed the 60s window by 8.97s on the venue clock because "
                "the limit quote carried a 21.86s-old venue tick while the wait was timed off "
                "the local receipt stamp. A met limit (ask 1.15 = limit 1.15) could not be "
                "credited."
            ),
            "status": "OPEN - future slots must aim the refresh off source updated_at.",
        },
        {
            "id": "PROVIDER_BAR_CONSOLIDATION_LAG_AND_NON_MONOTONICITY",
            "severity": "P1",
            "slots_affected": ["1003", "1023", "1043"],
            "detail": (
                "10:03 saw the newest fully-formed 5-minute bar 512s old, past the 420s limit "
                "(BAR_FRESHNESS_FAIL). SPY's 16:55Z bar volume DECREASED between reads 2.5 "
                "minutes apart (2786 -> 280). 10:23 and 10:43 could only partially re-verify "
                "consolidation, leaving several volume ratios as single-read lower bounds."
            ),
            "status": (
                "OPEN - immaterial to today's verdicts (no ratio sat close enough to a "
                "threshold for the difference to matter), but not asserted either way."
            ),
        },
        {
            "id": "STALE_VENUE_TICK_ON_FILL_OBSERVATION",
            "severity": "P2",
            "slots_affected": ["0943"],
            "detail": (
                "The 09:43 RIVN SIMULATED_FILL rests on two observations carrying a "
                "byte-identical source_updated_at, so the venue never re-ticked inside the "
                "window. The trajectory records the caveat itself."
            ),
            "status": "OPEN - fill machinery exercised, but this datapoint is one tick, not two.",
        },
        {
            "id": "BUDGET_UNIVERSE_MISMATCH",
            "severity": "P1",
            "slots_affected": ["0723", "1043"],
            "detail": (
                "The symbols that approach the volume gate are mega-caps whose "
                "near-the-money contracts are unaffordable at stage 1: IWM P290 at $456.00 "
                "and TSLA C307.5 at $1210.00 versus the $75 stage-1 eligibility budget "
                "(6.1x and 16.1x). TSLA also exceeds the $300 top research band by 4.03x."
            ),
            "status": "OPEN - a known structural tension between the universe and the capital ceiling.",
        },
        {
            "id": "SCHEDULER_INCIDENTS_UNACKNOWLEDGED",
            "severity": "P2",
            "detail": (
                "The two 2026-07-28 SCHEDULED_RUN_MISSED incidents remain open, holding "
                "scheduler-watchdog-scan at INCIDENT and new_entries_blocked at true for a "
                "third day. No 2026-07-29 or 2026-07-30 slot contributed to this."
            ),
            "status": "OPEN - owner acknowledgement required; this read-only worker cannot clear it.",
        },
    ],

    "constraints_observed": {
        "local_logs_only": True,
        "no_market_data_backfilled": True,
        "no_mcp_calls_made": True,
        "no_order_review_place_replace_cancel_transfer_or_mutation": True,
        "no_account_numbers_names_credentials_or_personal_data_stored": True,
        "pilot_and_drill_excluded_from_formal_performance": True,
        "start_ack_not_rewritten": True,
    },

    "notes": [
        "2026-07-30 is a clean-schedule, zero-policy-trade day: 17 of 17 runs launched and "
        "acknowledged, 0 missed, 0 incomplete trajectories.",
        "The one-per-day policy-trade budget went unconsumed for the third consecutive day; "
        "no symbol produced a qualifying signal.",
        "Every rejection at or above the frozen 1.50 volume gate today came from "
        "SIX_BAR_BREAKOUT_FAILED, not from volume - the same pattern as 2026-07-29.",
        "The 11:23 slot self-corrected a hand-rolled volume-ratio pass that would have "
        "reported AMD/AMZN above BASE_18; the canonical evaluator governed and no label fired. "
        "That correction is recorded in the slot's own summary.",
        "This run made ZERO MCP calls and fetched no market data, as CLOSE_SUMMARY requires.",
        "This run does not authorize formal Shadow; authorization is an owner-only action.",
    ],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
tmp = OUT.with_suffix(".tmp")
tmp.write_text(json.dumps(summary, indent=1, sort_keys=True))
tmp.replace(OUT)
print(json.dumps({"status": "WRITTEN", "path": str(OUT), "bytes": OUT.stat().st_size}))

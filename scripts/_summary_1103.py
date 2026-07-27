#!/usr/bin/env python3
"""Terminal summary writer for pilot-20260727-1103. Local data only."""
import json, os, datetime

RUN = "pilot-20260727-1103"
OUT = "logs/launchd_worker/2026-07-27/%s.summary.json" % RUN
os.makedirs(os.path.dirname(OUT), exist_ok=True)
TD = "logs/quote_trajectories/2026-07-27"

s = {
    "run_id": RUN,
    "run_kind": "PILOT_SAMPLE",
    "scheduled_time": "2026-07-27T11:03:00-07:00",
    "started_at": "2026-07-27T11:03:11-07:00",
    "completed_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "status": "SUCCESS",
    "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",

    "safety_preflight": {
        "verdict": "PASS - all four required conditions met before any MCP call",
        "system_mode": "READ_ONLY",
        "live_trading_enabled": False,
        "order_tools_enabled": False,
        "kill_switch_engaged": True,
        "kill_switch_reason": "TRADING_ARM_MARKER_ABSENT",
        "automation_halted": False,
        "approved_trade_stage": 1,
        "max_deployable_capital_usd": 300,
        "phase3_blockers": [],
        "source": "python3 main.py status, run as the first action of the slot",
    },

    "decision": "NO_TRADE",
    "decision_reasons": [
        "NO_TRADE: no symbol in the ten-symbol universe produced a REGIME-ALIGNED qualified signal. Regime is BEARISH, so the tradable direction is bearish, and SIX_BAR_BREAKDOWN failed on all ten symbols on the newest completed bar. Every close sits ABOVE its prior-6-bar low; the nearest were AAPL (0.0464 percent above) and META (0.0656 percent above). A near-breakdown is not a breakdown.",
        "MSFT satisfied every condition of the BULLISH rule INCLUDING volume (volume_ratio 1.5050 >= 1.50, the FIRST volume-confirmation PASS of the 2026-07-27 pilot), but a bullish expression is opposed by the BEARISH regime, and require_market_regime_alignment=true rejects it. The regime gate is a permitted NEAR_MISS dimension.",
        "MSFT was nonetheless NOT admitted as a NEAR_MISS, because it is additionally hard-rejected by the earnings blackout, which is OUTSIDE the permitted miss set. MSFT reports 2026-07-29 pm, verified=true, days_to_earnings=2 against earnings_blackout_calendar_days=3.",
        "MSFT is blocked by a third independent reason: its quote-latency upper bound of 14.443451s exceeds maximum_quote_age_seconds=10 and fails closed under unknown_required_field_rejects=true.",
        "Zero of the two permitted NEAR_MISS slots were consumed. No other symbol failed on permitted dimensions ALONE.",
        "The one virtual policy trade permitted per day remains UNCONSUMED on 2026-07-27, at 1 remaining. No slot has consumed it.",
    ],

    "frozen_paired_label_verdicts": {
        "BASE_25": "NO_TRADE - admits nothing this slot; shares the entire eligibility path with BASE_30 and diverges only at profit_target_option_pct (25.0 vs 30.0), which is unreachable without an entry.",
        "BASE_30": "NO_TRADE - admits nothing this slot, same eligibility path. The BASE_25 / BASE_30 pair has not diverged at any slot of the 2026-07-27 pilot, because no entry has been admitted on any slot.",
        "AI_RANK_V1": "ABSTAIN - ranking placed MSFT first on directional-plus-volume completeness, META second on the bearish side, AAPL third; it then abstained because the only complete setup is regime-opposed and earnings-blocked. AI ranked and abstained ONLY; it did not admit, override, weaken, or reinterpret any gate, and produced no candidate the deterministic rules had not already produced.",
        "NEAR_MISS": "0 of up to 2 slots consumed. MSFT is the only symbol whose sole permitted-set failure is the regime gate, and it is hard-rejected on earnings, so it was recorded as counterfactual evidence without consuming a slot.",
    },

    "universe_evaluation": {
        "symbols": ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"],
        "bar_interval_minutes": 5,
        "newest_completed_bar": "begins 2026-07-27T17:55:00Z, ends 18:00:00Z",
        "newest_completed_bar_lag_seconds_at_universe_read": 266.4,
        "newest_completed_bar_lag_limit_seconds": 420,
        "newest_completed_bar_lag_verdict": "PASS",
        "completed_bars_per_symbol": 54,
        "older_lookback_bars_per_symbol": 53,
        "freshness_rule_application": "The 420s limit was applied ONLY to the newest completed bar. The 53 older lookback bars feeding EMA9/EMA20, session VWAP, the 6-bar breakout window and the 20-bar volume mean had NO freshness rule applied, as required. The in-progress bar beginning 18:00:00Z was excluded by reject_incomplete_bars=true.",
        "market_regime": "BEARISH",
        "market_regime_basis": "Both reference symbols agree over the last 2 confirmation bars. SPY ema9 737.1056 < ema20 737.2626, closes 737.4982 and 737.80 both below session VWAP 739.5515. QQQ ema9 678.7536 < ema20 678.9011, closes 679.41 and 679.87 both below session VWAP 681.6572. Neither reference is mixed or unknown, so the regime is cleanly resolved and cleanly opposed to the only complete setup.",
        "per_symbol": {
            "SPY":  {"close": 737.80,   "vwap": 739.5515, "ema9": 737.1056, "ema20": 737.2626, "prior6_low": 736.18,   "prior6_high": 737.55,  "volume_ratio": 0.9033, "verdict": "NO_SIGNAL - bearish EMA/VWAP alignment but no breakdown (close above prior-6-bar low) and volume_ratio below 1.50"},
            "QQQ":  {"close": 679.87,   "vwap": 681.6572, "ema9": 678.7536, "ema20": 678.9011, "prior6_low": 677.26,   "prior6_high": 679.50,  "volume_ratio": 1.0140, "verdict": "NO_SIGNAL - no breakdown, volume_ratio below 1.50"},
            "IWM":  {"close": 292.47,   "vwap": 292.9847, "ema9": 292.0002, "ema20": 292.0304, "prior6_low": 291.585,  "prior6_high": 292.09,  "volume_ratio": 1.5034, "verdict": "NO_SIGNAL - volume_ratio PASSES at 1.5034 but no breakdown; close 292.47 is above the prior-6-bar low 291.585"},
            "AAPL": {"close": 335.5175, "vwap": 336.8645, "ema9": 336.0877, "ema20": 336.4431, "prior6_low": 335.362,  "prior6_high": 336.80,  "volume_ratio": 1.6280, "verdict": "NO_SIGNAL - volume_ratio PASSES at 1.6280, the highest in the universe, and bearish EMA/VWAP alignment holds, but close 335.5175 is 0.1555 ABOVE the prior-6-bar low 335.362, so SIX_BAR_BREAKDOWN fails by 0.0464 percent. Nearest miss in the universe."},
            "MSFT": {"close": 393.715,  "vwap": 391.1331, "ema9": 392.5252, "ema20": 391.9623, "prior6_low": 391.52,   "prior6_high": 393.27,  "volume_ratio": 1.5050, "verdict": "COMPLETE BULLISH SIGNAL, REGIME-OPPOSED AND EARNINGS-BLOCKED - close > VWAP, ema9 > ema20, close 393.715 > prior-6-bar high 393.27 (breakout margin 0.1131 percent), volume_ratio 1.5050 >= 1.50. Rejected on regime (permitted miss dimension) AND earnings blackout (hard, outside the permitted set) AND quote latency."},
            "NVDA": {"close": 196.795,  "vwap": 199.3222, "ema9": 196.2632, "ema20": 196.5178, "prior6_low": 195.44,   "prior6_high": 196.5499, "volume_ratio": 1.0088, "verdict": "NO_SIGNAL - no breakdown, volume_ratio below 1.50. Not re-admitted as a fresh candidate; only the 1023 trajectory close was written."},
            "AMZN": {"close": 232.26,   "vwap": 233.2047, "ema9": 231.9114, "ema20": 231.9287, "prior6_low": 231.49,   "prior6_high": 232.24,  "volume_ratio": 0.7816, "verdict": "NO_SIGNAL - no breakdown, weakest volume_ratio in the universe"},
            "META": {"close": 594.83,   "vwap": 602.6644, "ema9": 595.9024, "ema20": 597.2265, "prior6_low": 594.44,   "prior6_high": 597.53,  "volume_ratio": 1.4253, "verdict": "NO_SIGNAL - close 594.83 is 0.39 ABOVE the prior-6-bar low 594.44, so the breakdown that qualified META at the 1043 slot is no longer met; volume_ratio 1.4253 also below 1.50. Not re-admitted; only the 1043 trajectory refresh was written."},
            "TSLA": {"close": 308.44,   "vwap": 309.3791, "ema9": 307.9759, "ema20": 308.0307, "prior6_low": 307.22,   "prior6_high": 308.67,  "volume_ratio": 0.9020, "verdict": "NO_SIGNAL - no breakdown, volume_ratio below 1.50"},
            "AMD":  {"close": 483.36,   "vwap": 491.6689, "ema9": 481.5274, "ema20": 482.1617, "prior6_low": 478.7705, "prior6_high": 482.85,  "volume_ratio": 1.1075, "verdict": "NO_SIGNAL - no breakdown, volume_ratio below 1.50"},
        },
    },

    "current_quotes_snapshot": {
        "SPY": 737.83, "QQQ": 679.93, "IWM": 292.48, "AAPL": 335.335, "MSFT": 393.755,
        "NVDA": 197.01, "AMZN": 231.91, "META": 594.15, "TSLA": 307.93, "AMD": 483.49,
        "note": "live last_trade_price at 2026-07-27T18:04:10-15Z venue stamps; used for context only, NOT as signal inputs (signals use completed bars only)",
    },

    "trajectory_events_written": [
        {
            "path": "%s/pilot-20260727-1023-NVDA-P195-20260807.HORIZON_CLOSE.json" % TD,
            "trajectory_id": "pilot-20260727-1023-NVDA-P195-20260807",
            "event_type": "HORIZON_CLOSE",
            "outcome": "NO_FILL / NO_TRADE - trajectory now CLOSED, no further events owed",
            "note": "Discharges the HORIZON_CLOSE debt the 1043 slot deferred. Horizon was 2026-07-27T17:55:14.784901Z; this observation is 518.650283s later. The close price is the first observation available at or after the horizon, NOT a reconstruction of the price AT the horizon, and no interpolation was performed.",
        },
        {
            "path": "%s/pilot-20260727-1043-META-P595-20260807.QUOTE.json" % TD,
            "trajectory_id": "pilot-20260727-1043-META-P595-20260807",
            "event_type": "QUOTE",
            "outcome": "OPEN - horizon 2026-07-27T18:15:06.076567Z not yet reached, 672.641383s remaining",
            "note": "HORIZON_CLOSE is deliberately NOT written and is OWED TO A LATER SLOT. Closing early would require future data.",
        },
        {
            "path": "%s/pilot-20260727-1103-MSFT-C395-20260807.CANDIDATE.json" % TD,
            "trajectory_id": "pilot-20260727-1103-MSFT-C395-20260807",
            "event_type": "CANDIDATE",
            "outcome": "NOT ADMITTED - counterfactual research evidence only; no NEAR_MISS slot consumed, no entry test opened",
            "note": "Recorded so the only complete directional signal of the slot is preserved as evidence rather than discarded.",
        },
    ],
    "trajectory_schema_conformance": "ALL 15 files in %s validated against config/quote_trajectory.schema.json (required keys, additionalProperties=false, enums, const values, type unions, underlying pattern). ALL CONFORM. Note: the jsonschema module is not installed in this environment, so a structural checker implementing the schema's constraints was used instead of a reference validator." % TD,

    "open_trajectories_after_this_slot": [
        {
            "trajectory_id": "pilot-20260727-1043-META-P595-20260807",
            "owed_event": "HORIZON_CLOSE",
            "horizon_utc": "2026-07-27T18:15:06.076567Z",
            "note": "Due at or after 18:15:06Z, i.e. the 1123 slot is the first that can discharge it.",
        }
    ],

    "final_instrument_refreshes": [
        {"underlying": "NVDA", "contract": "P195 2026-08-07", "instrument_id": "5731f498-67ba-4401-b8e5-531d7922b701",
         "bid": 5.15, "ask": 5.25, "mark": 5.20, "volume": 2736, "open_interest": 3022,
         "implied_volatility": 0.458285, "delta": -0.42865, "gamma": 0.02503, "theta": -0.269721, "vega": 0.134728, "rho": -0.026206,
         "source_updated_at": "2026-07-27T18:03:52.304822468Z", "local_receipt_at": "2026-07-27T18:03:53.435184Z",
         "latency_bound_seconds": 1.130362, "latency_verdict": "PASS", "bid_size": 56, "ask_size": 114,
         "spread_absolute": 0.10, "spread_relative": 0.019231, "spread_verdict": "PASS"},
        {"underlying": "META", "contract": "P595 2026-08-07", "instrument_id": "b8f0d4c6-c99f-4816-a6bb-c1d8ddca2067",
         "bid": 27.25, "ask": 28.45, "mark": 27.85, "volume": 178, "open_interest": 396,
         "implied_volatility": 0.675246, "delta": -0.476459, "gamma": 0.005717, "theta": -1.22624, "vega": 0.412427, "rho": -0.089647,
         "source_updated_at": "2026-07-27T18:03:51.805803117Z", "local_receipt_at": "2026-07-27T18:03:53.435184Z",
         "latency_bound_seconds": 1.629381, "latency_verdict": "PASS", "bid_size": 125, "ask_size": 69,
         "spread_absolute": 1.20, "spread_relative": 0.043088, "spread_verdict": "PASS"},
        {"underlying": "MSFT", "contract": "C395 2026-08-07", "instrument_id": "6166810d-85d1-4c7b-9242-ad19ffe63e7c",
         "bid": 15.25, "ask": 15.60, "mark": 15.425, "volume": 507, "open_interest": 629,
         "implied_volatility": 0.580624, "delta": 0.510777, "gamma": 0.010023, "theta": -0.735715, "vega": 0.273488, "rho": 0.056345,
         "source_updated_at": "2026-07-27T18:05:01.101466662Z", "local_receipt_at": "2026-07-27T18:05:15.544917Z",
         "latency_bound_seconds": 14.443451, "latency_verdict": "FAIL CLOSED - exceeds maximum_quote_age_seconds=10", "bid_size": 12, "ask_size": 246,
         "spread_absolute": 0.35, "spread_relative": 0.022690, "spread_verdict": "PASS"},
    ],

    "data_freshness": {
        "latency_semantics": "Every latency figure is a MEASURED STRICT UPPER BOUND: the receipt stamp is taken at the first shell round trip after the MCP response, not at the instant of arrival. True latency is lower by an unmeasured amount. No receipt time was invented or back-dated to bring a bound inside the limit.",
        "NVDA_latency_bound_seconds": 1.130362,
        "NVDA_latency_verdict": "PASS - first and only passing bound on the NVDA trajectory, whose 1023 and 1043 observations both failed closed (11.608385s and 11.625162s).",
        "META_latency_bound_seconds": 1.629381,
        "META_latency_verdict": "PASS - second consecutive passing bound on the META trajectory (6.959655s at 1043).",
        "MSFT_latency_bound_seconds": 14.443451,
        "MSFT_latency_verdict": "FAIL CLOSED - exceeds maximum_quote_age_seconds=10 under unknown_required_field_rejects=true. Independent third blocking reason for MSFT, distinct from the earnings and regime failures.",
        "missing_values_invented": "none - every field is either an observed value or explicitly null/UNKNOWN",
    },

    "earnings_blackout_check": {
        "tool": "get_earnings_calendar(start_date=2026-07-27, days=7, filter=high_market_cap)",
        "rows_returned": 732,
        "universe_hits": {
            "MSFT": {"report_date": "2026-07-29", "timing": "pm", "verified": True, "days_to_earnings": 2, "verdict": "BLOCKED"},
            "META": {"report_date": "2026-07-29", "timing": "pm", "verified": True, "days_to_earnings": 2, "verdict": "BLOCKED"},
            "AAPL": {"report_date": "2026-07-30", "timing": "pm", "verified": True, "days_to_earnings": 3, "verdict": "BLOCKED"},
            "AMZN": {"report_date": "2026-07-30", "timing": "pm", "verified": True, "days_to_earnings": 3, "verdict": "BLOCKED"},
        },
        "blackout_window": "2026-07-27 through 2026-07-30 (earnings_blackout_calendar_days=3)",
        "note": "Four of the ten universe symbols are inside the blackout window and are hard-ineligible for the remainder of 2026-07-27 regardless of signal. AAPL is the nearest breakdown miss of this slot AND is earnings-blocked, so even a breakdown would not have admitted it. The remaining six symbols are calendar ABSENCES over the queried window, not positive clears; an absence is weaker evidence than the presence hits above and is recorded as such.",
    },

    "friction_observations": {
        "note": "All figures are counterfactual forced round trips. NO ENTRY OCCURRED on any trajectory, so none of these are realized. Entry always uses an observed ask and exit an observed bid; no mark was used as a fill price anywhere in this run.",
        "NVDA_P195_instant_base": "-0.10/share = -10.00 USD/contract = -1.9048 percent of the 5.25 entry. Lowest instantaneous friction of any contract examined this slot.",
        "NVDA_P195_instant_stress": "-0.20/share = -20.00 USD/contract = -3.7736 percent of the stressed 5.30 entry (one tick = 0.05 above the 3.00 cutoff price)",
        "NVDA_P195_full_horizon_counterfactual": "-0.80/share = -80.00 USD/contract = -13.4454 percent, buying the 1023 ask 5.95 and selling the horizon bid 5.15. The no-fill AVOIDED this loss, which more than doubled from -5.8824 percent at the 1043 midpoint.",
        "META_P595_instant_base": "-1.20/share = -120.00 USD/contract = -4.2179 percent of the 28.45 entry",
        "META_P595_instant_stress": "-1.30/share = -130.00 USD/contract = -4.5614 percent of the stressed 28.50 entry",
        "META_P595_partial_counterfactual": "-0.75/share = -75.00 USD/contract = -2.6786 percent, buying the 1043 ask 28.00 and selling the bid observed here 27.25. KEY RESULT OF THE SLOT: the META signal was directionally CORRECT (mark rose 27.50 to 27.85 as the underlying fell) and the trade is STILL a loss, because 0.35 of favourable movement did not cover crossing a 1.00-to-1.20 spread twice.",
        "MSFT_C395_instant_base": "-0.35/share = -35.00 USD/contract = -2.2436 percent of the 15.60 entry",
        "MSFT_C395_instant_stress": "-0.45/share = -45.00 USD/contract = -2.8754 percent of the stressed 15.65 entry",
        "no_fill_accounting": "3 of 3 trajectories touched this slot are NO_FILL. Across 2026-07-27 no simulated entry has occurred at any slot.",
    },

    "budget_and_capital": {
        "stage_1_maximum_premium_usd": 75,
        "max_deployable_capital_usd": 300,
        "cheapest_contract_examined_this_slot_usd": 525.0,
        "verdict": "Every contract examined this slot is outside the stage-1 premium ceiling: NVDA 195P at 525.00 USD (7.0x the cap), MSFT 395C at 1560.00 USD (20.8x), META 595P at 2845.00 USD (37.9x). The ten-symbol quality universe continues to produce NO contract inside the stage-1 ceiling. This is the structural finding config/calibration_universe.toml exists to measure separately, and it is a property of the universe design, not a data error.",
    },

    "policy_trade_budget": {
        "permitted_per_day": 1,
        "consumed_on_2026_07_27": 0,
        "remaining": 1,
        "note": "All events written on 2026-07-27 are counterfactual research trajectories, not policy trades.",
    },

    "timing_compliance": {
        "slot_start": "2026-07-27T11:03:11-07:00",
        "last_mcp_call_completed": "2026-07-27T11:05:15-07:00",
        "mcp_deadline": "2026-07-27T11:09:00-07:00 (six minutes)",
        "mcp_deadline_verdict": "PASS - all MCP calls finished with roughly 3.75 minutes of margin",
        "log_deadline": "2026-07-27T11:11:00-07:00 (eight minutes)",
        "log_deadline_verdict": "PASS",
    },

    "mcp_calls_made": [
        "get_option_quotes (2 instruments: NVDA 195P, META 595P) - unfinished-trajectory refresh, executed FIRST as required",
        "get_equity_historicals (10 symbols, 5minute, regular bounds, from 2026-07-27T13:30:00Z)",
        "get_equity_quotes (10 symbols)",
        "get_earnings_calendar (start_date=2026-07-27, days=7, filter=high_market_cap)",
        "get_option_quotes (1 instrument: MSFT 395C) - final instrument-specific refresh for the selected contract",
    ],

    "caveats": [
        "Every number in this file is PILOT_EXCLUDED_FROM_PERFORMANCE and must never be aggregated into any formal result.",
        "No order was reviewed, placed, replaced, or cancelled. No watchlist or account state was mutated. Only official Robinhood get_* tools were called.",
        "No account number, name, credential, token, or personal datum was read into or written from this run. The account-scoped tools were not called at all.",
        "All entry tests used an observed ask and all exit tests an observed bid. No mark was ever used as a fill price.",
        "PASS, FAIL, and UNKNOWN verdicts are preserved verbatim; no missing value was invented or defaulted.",
        "This run does not authorize formal Shadow and did not perform Shadow qualification.",
        "One trajectory (pilot-20260727-1043-META-P595-20260807) remains OPEN and owes a HORIZON_CLOSE at or after 2026-07-27T18:15:06.076567Z.",
    ],
}

with open(OUT, "w") as f:
    json.dump(s, f, indent=2, sort_keys=True)
    f.write("\n")
print("WROTE", OUT)

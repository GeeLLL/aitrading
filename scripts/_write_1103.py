#!/usr/bin/env python3
"""One-shot writer for the pilot-20260727-1103 slot. Read-only inputs already
collected; this only serializes observed values to disk."""
import json, os, datetime

D = "logs/quote_trajectories/2026-07-27"
os.makedirs(D, exist_ok=True)
EV = "PILOT_OBSERVED_QUOTE_EXCLUDED_FROM_PERFORMANCE"


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def secs(a, b):
    return round((ts(a) - ts(b)).total_seconds(), 6)


def write(ev):
    p = os.path.join(D, "%s.%s.json" % (ev["trajectory_id"], ev["event_type"]))
    with open(p, "w") as f:
        json.dump(ev, f, indent=2)
        f.write("\n")
    print("WROTE", p)
    return p


# ---------------------------------------------------------------- observations
RCPT_A = "2026-07-27T18:03:53.435184Z"   # NVDA + META batch receipt stamp
RCPT_B = "2026-07-27T18:05:15.544917Z"   # MSFT receipt stamp (strict upper bound)

nvda_dec = "2026-07-27T17:25:14.784901Z"
meta_dec = "2026-07-27T17:45:06.076567Z"

nvda_lat = secs(RCPT_A, "2026-07-27T18:03:52.304822468Z")
meta_lat = secs(RCPT_A, "2026-07-27T18:03:51.805803117Z")
msft_lat = secs(RCPT_B, "2026-07-27T18:05:01.101466662Z")

nvda_age = secs(RCPT_A, nvda_dec)
meta_age = secs(RCPT_A, meta_dec)
meta_left = round(1800 - meta_age, 6)
nvda_past = round(nvda_age - 1800, 6)

print("latencies nvda=%s meta=%s msft=%s" % (nvda_lat, meta_lat, msft_lat))
print("nvda_past_horizon=%s meta_remaining=%s" % (nvda_past, meta_left))

msft_vr = 146049 / 97039.9

# ------------------------------------------------------- 1) NVDA HORIZON_CLOSE
nvda = {
    "schema_version": 1,
    "trajectory_id": "pilot-20260727-1023-NVDA-P195-20260807",
    "event_type": "HORIZON_CLOSE",
    "policy_labels": ["NEAR_MISS"],
    "instrument_id": "5731f498-67ba-4401-b8e5-531d7922b701",
    "underlying": "NVDA",
    "option_type": "put",
    "strike": 195.0,
    "expiration_date": "2026-08-07",
    "decision_time": nvda_dec,
    "quote_received_at": RCPT_A,
    "source_updated_at": "2026-07-27T18:03:52.304822468Z",
    "bid": 5.15,
    "ask": 5.25,
    "mark": 5.2,
    "volume": 2736,
    "open_interest": 3022,
    "delta": -0.42865,
    "implied_volatility": 0.458285,
    "theta": -0.269721,
    "target_horizon_minutes": 30,
    "evidence_class": EV,
    "rejection_reasons": [
        "REFRESH_OF_UNFINISHED_TRAJECTORY_FIRST: this is the mandated first action of the 1103 slot. The trajectory was opened at the 1023 slot (CANDIDATE at %s) and refreshed once at the 1043 slot (QUOTE). The original trajectory_id is preserved; this slot did not open a new NVDA trajectory." % nvda_dec,
        "HORIZON_CLOSE_IS_NOW_DUE_AND_IS_WRITTEN_HERE: target_horizon_minutes=30 from decision_time %s set the horizon at 2026-07-27T17:55:14.784901Z. This observation is stamped %s, which is %ss AFTER the horizon and %ss after decision_time. The 1043 slot explicitly deferred this close because the horizon had not yet elapsed; that debt is discharged now, on observed data, with no backfill and no interpolation to the exact horizon instant. The close price recorded is the first observation available at or after the horizon, not a reconstruction of the price AT the horizon, and it is %ss later than the horizon instant." % (nvda_dec, RCPT_A, nvda_past, nvda_age, nvda_past),
        "NO_SIMULATED_ENTRY_EVER_OCCURRED_SO_THERE_IS_NO_EXIT: the recorded simulated entry limit was 5.90. The ask test was satisfied for the first and only time at the 1043 observation (ask 5.75 <= 5.90), but strategy_v1.0.toml sets maximum_fill_wait_seconds=60, maximum_reprices=0 and unfilled_order_action=CANCEL_AND_NO_TRADE, so the limit order was cancelled at 2026-07-27T17:26:14.784901Z, which is 1031.0s BEFORE that qualifying ask appeared (the 1043 observation was 1091.0s after decision_time). This trajectory therefore closes NO_FILL / NO_TRADE. There is no position, no exit fill, and no realized P&L. No mark was used as a fill price at any point in this trajectory.",
        "ASK_AT_CLOSE_ALSO_BELOW_THE_LIMIT_AND_ALSO_IRRELEVANT: the ask observed here, 5.25, is likewise at or below the 5.90 limit. It is recorded so the file cannot be read as though the limit was never reachable, but it is equally unfillable for the same cancelled-order reason. Reachability of a limit after cancellation is not a fill.",
        "COUNTERFACTUAL_NO_FILL_AVOIDED_A_LARGER_LOSS: had the 1023 observation been force-filled at its observed ask 5.95 and exited at the bid observed here, 5.15, the round trip would be -0.80 per share = -80.00 USD per contract = -13.4454 percent of the 5.95 entry. At the 1043 observation the same counterfactual stood at -5.8824 percent, so the no-fill avoided a loss that more than doubled over the horizon. Reported for symmetry with the 0943 MSFT trajectory, where the no-fill instead foregave a gain; suppressing either direction would bias the friction study.",
        "COUNTERFACTUAL_AT_THIS_INSTANT: a forced round trip at this observation alone (buy the 5.25 ask, sell the 5.15 bid) would cost -0.10 per share = -10.00 USD per contract = -1.9048 percent of the 5.25 entry, with no price move required. Under a one-tick adverse stress (min_ticks above_tick=0.05 above the 3.00 cutoff price, so 0.05 is the true increment) entry 5.30 and exit 5.10 would cost -0.20 per share = -20.00 USD per contract = -3.7736 percent of the stressed 5.30 entry.",
        "QUOTE_LATENCY_PASSES_FOR_THE_FIRST_TIME_ON_THIS_TRAJECTORY: source_updated_at 2026-07-27T18:03:52.304822468Z to receipt stamp %s = %ss, a MEASURED strict UPPER BOUND on true local receipt, not true arrival latency. It is INSIDE maximum_quote_age_seconds=10. Both prior observations on this trajectory failed closed on freshness (11.608385s at 1023, 11.625162s at 1043), so the closing observation is the only one of the three that satisfies the freshness gate. No receipt time was invented or back-dated." % (RCPT_A, nvda_lat),
        "SPREAD_NARROWED_BACK: absolute spread 0.10, versus 0.15 at the 1043 observation and 0.10 at 1023; relative 0.10 over mark 5.20 = 0.019231, comfortably inside maximum_relative_spread=0.05. Bid size 56 against ask size 114, an inversion of the 1043 reading (312 bid against 76 ask).",
        "UNDERLYING_CONTINUED_AGAINST_THE_PUT_ACROSS_THE_WHOLE_HORIZON: NVDA closed 195.56 on the 1023 decision bar, 195.95 at the 1043 slot, and 196.795 on the newest completed bar of this slot (begins 2026-07-27T17:55:00Z, ends 18:00:00Z). The put decayed monotonically across the three observations, mark 5.90 -> 5.675 -> 5.20, with delta -0.46475 -> -0.457038 -> -0.42865 and IV 0.462458 -> 0.456646 -> 0.458285. Contract volume rose 2551 -> 2668 -> 2736 while open_interest was unchanged at 3022 throughout.",
        "NVDA_STILL_FAILS_THE_BEARISH_RULE_THIS_SLOT: on the newest completed bar NVDA close 196.795 is ABOVE the prior-6-bar low 195.44, so SIX_BAR_BREAKDOWN is not met, and volume_ratio 1.0088 is below minimum_volume_ratio=1.50. NVDA was not re-admitted as a fresh candidate at this slot; only this closing event was written.",
        "TRAJECTORY_IS_NOW_CLOSED: no further events are owed on pilot-20260727-1023-NVDA-P195-20260807.",
        "STILL_NOT_A_POLICY_TRADE: this was a counterfactual research trajectory for its entire life. The one virtual policy trade permitted per day remains UNCONSUMED on 2026-07-27, at 1 remaining.",
        "NO_FUTURE_DATA: every field is an observed value stamped at or before the receipt time above.",
    ],
}

# ---------------------------------------------------------------- 2) META QUOTE
meta = {
    "schema_version": 1,
    "trajectory_id": "pilot-20260727-1043-META-P595-20260807",
    "event_type": "QUOTE",
    "policy_labels": ["NEAR_MISS"],
    "instrument_id": "b8f0d4c6-c99f-4816-a6bb-c1d8ddca2067",
    "underlying": "META",
    "option_type": "put",
    "strike": 595.0,
    "expiration_date": "2026-08-07",
    "decision_time": meta_dec,
    "quote_received_at": RCPT_A,
    "source_updated_at": "2026-07-27T18:03:51.805803117Z",
    "bid": 27.25,
    "ask": 28.45,
    "mark": 27.85,
    "volume": 178,
    "open_interest": 396,
    "delta": -0.476459,
    "implied_volatility": 0.675246,
    "theta": -1.22624,
    "target_horizon_minutes": 30,
    "evidence_class": EV,
    "rejection_reasons": [
        "REFRESH_OF_UNFINISHED_TRAJECTORY_FIRST: this is the mandated first action of the 1103 slot. The trajectory was opened at the 1043 slot (CANDIDATE at %s) and had no follow-up observation. The original trajectory_id is preserved; this slot did not open a new META trajectory." % meta_dec,
        "TRAJECTORY_REMAINS_OPEN_HORIZON_NOT_YET_REACHED: target_horizon_minutes=30 from decision_time %s sets the horizon at 2026-07-27T18:15:06.076567Z. This observation is stamped %s, which is %ss after decision_time and still %ss SHORT of the horizon. HORIZON_CLOSE is deliberately NOT written here and is owed to a later slot. Closing it early would require either future data or an invented observation." % (meta_dec, RCPT_A, meta_age, meta_left),
        "HARD_REJECTIONS_ALL_RE_CONFIRMED_ON_FRESH_DATA: the three hard eligibility failures recorded at 1043 were re-verified at this slot and all three still BLOCK. This event remains counterfactual research evidence; it is NOT an admitted candidate and it did NOT consume a NEAR_MISS slot.",
        "EARNINGS_BLACKOUT_ACTIVE_HARD_REJECT: META reports 2026-07-29 pm with report.verified=true, re-confirmed at this slot from get_earnings_calendar(start_date=2026-07-27, days=7, filter=high_market_cap, 732 rows returned, entry {\"symbol\": \"META\", \"year\": 2026, \"quarter\": 2, \"eps\": {\"estimate\": \"7.180000\", \"actual\": null}, \"report\": {\"date\": \"2026-07-29\", \"timing\": \"pm\", \"verified\": true}}). days_to_earnings=2; earnings_blackout_calendar_days=3 and the blackout window is 2026-07-27 through 2026-07-30. This is a HARD eligibility rejection, not a near-miss dimension. META is a PRESENCE hit in the calendar, so this rests on positive evidence rather than on absence.",
        "MINIMUM_OPTION_VOLUME_STILL_FAILED: contract volume 178 against minimum_option_volume=500, up by exactly 1 contract from the 177 observed at 1043 over 18.8 minutes. This remains the least liquid contract examined in the 2026-07-27 pilot; contrast NVDA 195P at 2736 and MSFT 395C at 507 in this same slot.",
        "MINIMUM_OPEN_INTEREST_STILL_FAILED: open_interest 396 against minimum_open_interest=500, unchanged from 1043. Recorded separately from the volume failure rather than folded into it.",
        "NO_ENTRY_TEST_WAS_EVER_OPENED_SO_NO_ASK_IS_ADJUDICATED: because the contract is hard-rejected, the limit implied by limit_formula=MID_PLUS_25_PERCENT_OF_SPREAD at the 1043 decision (27.50 + 0.25 x 1.00 = 27.75) was never live. For completeness the ask observed here, 28.45, is ABOVE that hypothetical limit, so even had a test been opened the raw ask condition would FAIL at this observation; and it would in any case have been cancelled at 2026-07-27T17:46:06.076567Z under maximum_fill_wait_seconds=60. NO ENTRY OCCURRED. No mark fill was assumed.",
        "QUOTE_LATENCY_PASSES: source_updated_at 2026-07-27T18:03:51.805803117Z to receipt stamp %s = %ss, a MEASURED strict UPPER BOUND on true local receipt, not true arrival latency. It is INSIDE maximum_quote_age_seconds=10, so the contract-quote freshness gate PASSES, as it did at 1043 (6.959655s)." % (RCPT_A, meta_lat),
        "SPREAD_WIDENED_BUT_STILL_PASSES: absolute spread 1.20 versus 1.00 at 1043; relative 1.20 over mark 27.85 = 0.043088, against maximum_relative_spread=0.05. PASS, but the margin to the cap has narrowed from 0.013636 to 0.006912. Bid size 125 against ask size 69.",
        "PUT_GAINED_AS_UNDERLYING_FELL: META closed 595.915 on the 1043 decision bar and 594.83 on the newest completed bar of this slot (begins 2026-07-27T17:55:00Z, ends 18:00:00Z). The put appreciated accordingly, mark 27.50 to 27.85, with delta -0.472687 to -0.476459 and IV 0.674054 to 0.675246. This is the direction the 1043 signal predicted, which is precisely why the hard rejection is worth recording: the blocked trajectory is currently the profitable one.",
        "COUNTERFACTUAL_NO_FILL_FOREGAVE_A_GAIN_ON_THE_ASK_TEST_BUT_NOT_ON_FRICTION: had a fill been forced at the 1043 observed ask 28.00 and exited at the bid observed here, 27.25, the round trip would still be -0.75 per share = -75.00 USD per contract = -2.6786 percent of the 28.00 entry. The 0.35 of favourable mark movement did not cover the 1.00-to-1.20 spread. This is the clearest friction result of the day so far: a correctly-signalled directional move that is still a loss after crossing the spread twice.",
        "COUNTERFACTUAL_AT_THIS_INSTANT: a forced round trip at this observation alone (buy the 28.45 ask, sell the 27.25 bid) would cost -1.20 per share = -120.00 USD per contract = -4.2179 percent of the 28.45 entry, with no price move required. Under a one-tick adverse stress (min_ticks above_tick=0.05 above the 3.00 cutoff price, so 0.05 is the true increment) entry 28.50 and exit 27.20 would cost -1.30 per share = -130.00 USD per contract = -4.5614 percent of the stressed 28.50 entry.",
        "PREMIUM_STILL_EXCEEDS_STAGE_1_BUDGET: ask 28.45 x100 = 2845.00 USD per contract against stage_1_maximum_premium_usd=75 and max_deployable_capital_usd=300. Roughly 37.9x the stage-1 premium cap and 9.5x total deployable capital.",
        "META_NO_LONGER_SATISFIES_THE_BEARISH_RULE_THIS_SLOT: on the newest completed bar META close 594.83 is ABOVE the prior-6-bar low 594.44, so SIX_BAR_BREAKDOWN is no longer met, and volume_ratio has fallen to 1.4253 from 1.2759 measured against a different window but still below minimum_volume_ratio=1.50. META was not re-admitted as a fresh candidate at this slot; only this refresh event was written.",
        "STILL_NOT_A_POLICY_TRADE: this remains a counterfactual research trajectory. The one virtual policy trade permitted per day remains UNCONSUMED on 2026-07-27, at 1 remaining.",
        "NO_FUTURE_DATA: every field is an observed value stamped at or before the receipt time above.",
    ],
}

# ------------------------------------------------------------ 3) MSFT CANDIDATE
msft = {
    "schema_version": 1,
    "trajectory_id": "pilot-20260727-1103-MSFT-C395-20260807",
    "event_type": "CANDIDATE",
    "policy_labels": ["NEAR_MISS"],
    "instrument_id": "6166810d-85d1-4c7b-9242-ad19ffe63e7c",
    "underlying": "MSFT",
    "option_type": "call",
    "strike": 395.0,
    "expiration_date": "2026-08-07",
    "decision_time": RCPT_B,
    "quote_received_at": RCPT_B,
    "source_updated_at": "2026-07-27T18:05:01.101466662Z",
    "bid": 15.25,
    "ask": 15.6,
    "mark": 15.425,
    "volume": 507,
    "open_interest": 629,
    "delta": 0.510777,
    "implied_volatility": 0.580624,
    "theta": -0.735715,
    "target_horizon_minutes": 30,
    "evidence_class": EV,
    "rejection_reasons": [
        "NOT_ADMITTED_AS_NEAR_MISS_CANDIDATE: the frozen NEAR_MISS basis permits a candidate to miss ONLY on volume_ratio and/or the regime gate. MSFT misses on the regime gate, which IS inside the permitted set, but it additionally fails the earnings blackout, which is OUTSIDE it. This event is recorded to preserve the observed quote as counterfactual research evidence; it is NOT an admitted candidate and it did NOT consume a NEAR_MISS slot. This follows the precedent set by the 0943 MSFT and 1043 META events, hard-rejected on the same blackout ground.",
        "EARNINGS_BLACKOUT_ACTIVE_HARD_REJECT: MSFT reports 2026-07-29 pm with report.verified=true, re-verified at this slot from get_earnings_calendar(start_date=2026-07-27, days=7, filter=high_market_cap, 732 rows returned, entry {\"symbol\": \"MSFT\", \"year\": 2026, \"quarter\": 4, \"eps\": {\"estimate\": \"4.230000\", \"actual\": null}, \"report\": {\"date\": \"2026-07-29\", \"timing\": \"pm\", \"verified\": true}}). days_to_earnings=2; earnings_blackout_calendar_days=3 and strategy/shadow_pipeline.py rejects when 0 <= days_to_earnings <= 3. The blackout window is 2026-07-27 through 2026-07-30. This is a HARD eligibility rejection, NOT a near-miss dimension. The elevated implied volatility on this contract, 0.580624 against 0.458285 on the NVDA contract in this same slot, is consistent with an event premium two sessions ahead and is exactly the exposure the blackout gate exists to refuse.",
        "MSFT_IS_THE_ONLY_SYMBOL_IN_THE_TEN_SYMBOL_UNIVERSE_TO_SATISFY_A_COMPLETE_DIRECTIONAL_RULE_THIS_SLOT: measured on the newest completed 5-minute bar (begins 2026-07-27T17:55:00Z, ends 18:00:00Z), MSFT satisfies every condition of the frozen BULLISH rule including volume. close 393.715 > session VWAP 391.1331; ema9 392.5252 > ema20 391.9623; close 393.715 > prior-6-bar high 393.27, so SIX_BAR_BREAKOUT is met by a margin of 0.445, i.e. 0.1131 percent; volume_ratio %.4f >= minimum_volume_ratio=1.50. This is the FIRST event of the 2026-07-27 pilot in which the volume confirmation PASSES rather than being the miss dimension. The margin is thin: 146049 against a prior-20-bar mean of 97039.9, exceeding the floor by 0.005041 of ratio, i.e. 489.15 contracts of bar volume. Recorded explicitly because a pass this narrow would flip on a small revision.",
        "REGIME_GATE_FAILED_DIRECTION_OPPOSED: market_regime=BEARISH on both reference symbols over the last 2 confirmation bars, so a BULLISH expression is regime-opposed and require_market_regime_alignment=true rejects it. SPY ema9 737.1056 < ema20 737.2626 with closes 737.4982 and 737.80 both below session VWAP 739.5515. QQQ ema9 678.7536 < ema20 678.9011 with closes 679.41 and 679.87 both below session VWAP 681.6572. Neither reference symbol is mixed or unknown, so mixed_or_unknown_means_no_trade is not the operative clause; the regime is cleanly resolved and cleanly opposed. This is the permitted near-miss dimension, and it is the ONLY permitted-set dimension MSFT fails.",
        "NO_SYMBOL_IN_THE_UNIVERSE_SATISFIED_THE_REGIME_ALIGNED_BEARISH_RULE: with regime BEARISH the tradable direction is bearish, and SIX_BAR_BREAKDOWN failed on all ten symbols on the newest completed bar. Closes against prior-6-bar lows: SPY 737.80 vs 736.18, QQQ 679.87 vs 677.26, IWM 292.47 vs 291.585, AAPL 335.5175 vs 335.362, MSFT 393.715 vs 391.52, NVDA 196.795 vs 195.44, AMZN 232.26 vs 231.49, META 594.83 vs 594.44, TSLA 308.44 vs 307.22, AMD 483.36 vs 478.7705. Every close is ABOVE its prior-6-bar low. The two nearest were AAPL (0.1555 above, 0.0464 percent) and META (0.39 above, 0.0656 percent); neither is a breakdown, and a near-breakdown is not a breakdown. No bearish candidate existed to admit.",
        "BASE_25_VERDICT_NO_TRADE: the frozen BASE_25 label (profit_target_option_pct=25.0) admits nothing this slot. It shares the entire eligibility path with BASE_30 and diverges only at the exit target, so with no admitted entry the two labels are necessarily identical here. NO_TRADE.",
        "BASE_30_VERDICT_NO_TRADE: the frozen BASE_30 label (profit_target_option_pct=30.0, the strategy_v1.0.toml default) admits nothing this slot, for the same reason. NO_TRADE. The BASE_25 / BASE_30 pair has not diverged at any slot of the 2026-07-27 pilot, because no entry has been admitted on any slot.",
        "AI_RANK_V1_VERDICT_ABSTAIN: AI ranking placed MSFT first on directional-plus-volume completeness, META second on the bearish side, and AAPL third. It then ABSTAINED, because the only complete setup is regime-opposed and earnings-blocked, and both of those are deterministic gates the AI is not permitted to relax. AI ranking was used ONLY to order and to abstain; it did not admit, override, weaken, or reinterpret any gate, and it produced no candidate the deterministic rules had not already produced.",
        "NEAR_MISS_SLOTS_USED_ZERO_OF_TWO: up to two NEAR_MISS candidates were permitted this slot. Zero were admitted, because the only symbol whose sole permitted-set failure is the regime gate is hard-rejected on earnings, and no other symbol failed on permitted dimensions ALONE. Recording MSFT here does not consume a slot.",
        "CONTRACT_LIQUIDITY_GATES_PASS: volume 507 against minimum_option_volume=500 and open_interest 629 against minimum_open_interest=500. Both PASS, and both are recorded so the failure set is not overstated. The volume pass is narrow, exceeding the floor by 7 contracts. This is the first contract of the 2026-07-27 pilot to clear BOTH liquidity floors; the NVDA 195P clears volume and OI comfortably but was never regime-eligible as a fresh candidate this slot, and the META 595P fails both.",
        "DELTA_AND_DTE_IN_BAND: absolute delta 0.510777 is inside minimum_absolute_delta=0.30 and maximum_absolute_delta=0.65. DTE is 11 calendar days (2026-07-27 to 2026-08-07), inside main_dte_min=7 and main_dte_max=21. Both PASS.",
        "OBSERVED_SPREAD_PASSES: absolute 0.35, relative 0.35 over mark 15.425 = 0.022690, against maximum_relative_spread=0.05. PASS. Bid size 12 against ask size 246, a heavily one-sided book in which the resting bid is thin.",
        "QUOTE_LATENCY_FAILS_CLOSED: source_updated_at 2026-07-27T18:05:01.101466662Z to receipt stamp %s = %ss, a MEASURED strict UPPER BOUND on true local receipt, not true arrival latency; the true latency is smaller by an unmeasured amount. The bound EXCEEDS maximum_quote_age_seconds=10, so under unknown_required_field_rejects=true this observation FAILS CLOSED on freshness and could not have supported an entry even had the earnings and regime gates been clear. No receipt time was invented or back-dated to manufacture a pass. This is an INDEPENDENT third blocking reason, distinct from the earnings and regime failures." % (RCPT_B, msft_lat),
        "PREMIUM_EXCEEDS_STAGE_1_BUDGET: ask 15.60 x100 = 1560.00 USD per contract against stage_1_maximum_premium_usd=75 and max_deployable_capital_usd=300. Roughly 20.8x the stage-1 premium cap and 5.2x total deployable capital. Cheaper than the META 595P at 2845.00 but still far outside the budget; the ten-symbol quality universe continues to produce no contract inside the stage-1 ceiling, which is the structural finding config/calibration_universe.toml exists to measure separately.",
        "NO_SIMULATED_ENTRY: the limit implied by limit_formula=MID_PLUS_25_PERCENT_OF_SPREAD would be 15.425 + 0.25 x 0.35 = 15.5125. NO ENTRY OCCURRED and NO ENTRY TEST IS OPENED, so no later ask will be adjudicated against 15.5125 on this trajectory. No mark fill was assumed.",
        "COUNTERFACTUAL_BASE_FRICTION: had a fill been forced at the observed ask 15.60 and exited at the observed bid 15.25, round-trip base friction would be -0.35 per share = -35.00 USD per contract = -2.2436 percent of the 15.60 entry, with no price move required. This is the lowest instantaneous friction cost of any contract examined in the 2026-07-27 pilot, against -4.2179 percent on the META 595P and -1.9048 percent on the NVDA 195P at this same slot; the NVDA contract is in fact cheaper still.",
        "STRESS_FRICTION: under a one-tick-wider adverse stress (entry 15.65, exit 15.20; min_ticks above_tick=0.05 above the 3.00 cutoff price, so 0.05 is the true increment) the same forced round trip would cost -0.45 per share = -45.00 USD per contract = -2.8754 percent of the stressed 15.65 entry.",
        "NOT_A_POLICY_TRADE_BUDGET_UNTOUCHED: this is counterfactual research evidence, not the one virtual policy trade permitted per day. That budget was not consumed at any slot on 2026-07-27 and remains at 1 remaining.",
        "NO_FUTURE_DATA: every signal input is from the newest COMPLETED 5-minute bar ending 2026-07-27T18:00:00Z or earlier, plus a contract quote stamped 18:05:01.101466662Z. The newest completed bar's age at decision is 315.5s against maximum_latest_completed_bar_lag_seconds=420, and the 420s rule was applied ONLY to that newest bar, not to the 53 older lookback bars used for the EMA, VWAP, breakout and volume windows.",
    ],
}
msft["rejection_reasons"][2] = msft["rejection_reasons"][2] % msft_vr

for e in (nvda, meta, msft):
    write(e)

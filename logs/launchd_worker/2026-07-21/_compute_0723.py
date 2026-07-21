"""Deterministic indicator computation for pilot-20260721-0723 (PILOT_SAMPLE).

Read-only. Consumes the immutable historicals payload already returned by the
official MCP call; performs no network access of its own.

Parameters are taken from strategy/strategy_v1.0.toml. Label thresholds
BASE_25 / BASE_30 come from research/ACCELERATED_PROFITABILITY_AND_AI_EDGE_V1_CN.md,
consistent with run pilot-20260721-0703.
"""

import json
import sys
from datetime import datetime, timezone

PATH = sys.argv[1]

FAST_EMA = 9
SLOW_EMA = 20
VOL_LOOKBACK = 20
BREAKOUT_LOOKBACK = 6
REGIME_SYMBOLS = ["SPY", "QQQ"]
REGIME_CONFIRM_BARS = 2
TODAY = "2026-07-21"

with open(PATH) as fh:
    payload = json.load(fh)


def ema(values, period):
    """EMA seeded from the simple average of the first `period` values."""
    if len(values) < period:
        return None
    seed = sum(values[:period]) / period
    out = seed
    k = 2.0 / (period + 1.0)
    for v in values[period:]:
        out = v * k + out * (1.0 - k)
    return out


def session_vwap(bars):
    """Volume-weighted typical price over today's regular-hours bars.

    NOTE: VWAP is not supplied by the official MCP bar feed and is not pinned
    in strategy_v1.0.toml. This derivation is an unpinned choice (carried
    forward unchanged from pilot-20260721-0703) and is flagged as a caveat.
    """
    num = 0.0
    den = 0.0
    for b in bars:
        if not b["begins_at"].startswith(TODAY):
            continue
        tp = (float(b["high_price"]) + float(b["low_price"]) + float(b["close_price"])) / 3.0
        num += tp * b["volume"]
        den += b["volume"]
    return (num / den) if den else None


report = {}

for res in payload["data"]["results"]:
    sym = res["symbol"]
    bars = res["bars"]
    closes = [float(b["close_price"]) for b in bars]
    vols = [b["volume"] for b in bars]

    newest = bars[-1]
    newest_close = float(newest["close_price"])
    newest_vol = newest["volume"]

    # Volume ratio: newest completed bar vs average of the PRIOR 20 bars.
    prior_vols = vols[-(VOL_LOOKBACK + 1):-1]
    vol_avg = sum(prior_vols) / len(prior_vols) if len(prior_vols) == VOL_LOOKBACK else None
    vol_ratio = (newest_vol / vol_avg) if vol_avg else None

    # Breakout: newest close vs max high of the 6 bars preceding it.
    prior_bars = bars[-(BREAKOUT_LOOKBACK + 1):-1]
    prior_high = max(float(b["high_price"]) for b in prior_bars)
    prior_low = min(float(b["low_price"]) for b in prior_bars)
    breakout_up = newest_close > prior_high
    breakout_down = newest_close < prior_low

    ema_fast = ema(closes, FAST_EMA)
    ema_slow = ema(closes, SLOW_EMA)
    vwap = session_vwap(bars)

    ema_aligned_up = ema_fast is not None and ema_slow is not None and ema_fast > ema_slow
    ema_aligned_down = ema_fast is not None and ema_slow is not None and ema_fast < ema_slow
    vwap_up = vwap is not None and newest_close > vwap
    vwap_down = vwap is not None and newest_close < vwap

    # Directional confirmation over the last N completed bars.
    confirm = bars[-REGIME_CONFIRM_BARS:]
    confirm_up = all(float(b["close_price"]) > vwap for b in confirm) if vwap else False
    confirm_down = all(float(b["close_price"]) < vwap for b in confirm) if vwap else False

    report[sym] = {
        "newest_bar_begins_at": newest["begins_at"],
        "newest_close": newest_close,
        "newest_volume": newest_vol,
        "volume_average_20": round(vol_avg, 4) if vol_avg else None,
        "volume_ratio": round(vol_ratio, 4) if vol_ratio else None,
        "prior_6_high": prior_high,
        "prior_6_low": prior_low,
        "breakout_up": breakout_up,
        "breakout_down": breakout_down,
        "ema_9": round(ema_fast, 4) if ema_fast else None,
        "ema_20": round(ema_slow, 4) if ema_slow else None,
        "ema_aligned_up": ema_aligned_up,
        "ema_aligned_down": ema_aligned_down,
        "session_vwap": round(vwap, 4) if vwap else None,
        "close_above_vwap": vwap_up,
        "close_below_vwap": vwap_down,
        "confirm_up": confirm_up,
        "confirm_down": confirm_down,
    }

# Market regime gate: both reference symbols must agree, with EMA and VWAP
# alignment plus N-bar confirmation. Mixed or unknown => NO_TRADE.
ups = [report[s]["ema_aligned_up"] and report[s]["confirm_up"] for s in REGIME_SYMBOLS]
downs = [report[s]["ema_aligned_down"] and report[s]["confirm_down"] for s in REGIME_SYMBOLS]

if all(ups):
    regime = "DIRECTIONAL_UP"
elif all(downs):
    regime = "DIRECTIONAL_DOWN"
else:
    regime = "NOT_DIRECTIONAL"

out = {
    "regime": regime,
    "regime_detail": {s: {k: report[s][k] for k in
                          ("ema_9", "ema_20", "ema_aligned_up", "ema_aligned_down",
                           "session_vwap", "newest_close", "confirm_up", "confirm_down")}
                      for s in REGIME_SYMBOLS},
    "symbols": report,
}
print(json.dumps(out, indent=2, sort_keys=True))

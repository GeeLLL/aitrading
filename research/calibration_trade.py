"""Deterministic daily calibration trade (machinery validation, never evidence).

Ports the pilot-prompt spec verbatim into code so the guaranteed one-virtual-
fill-per-day lifecycle survives the removal of the LLM pilot agent:

  ENTRY  — highest volume_ratio universe symbol this run (no signal required);
           from its vaulted option snapshot pick the contract with |delta| in
           [0.30, 0.65] closest to 0.50 (tie: higher open_interest), preferring
           premium (mark x 100) <= $75, else <= $120, else <= $300; HARD
           liquidity floor volume >= 100 AND open_interest >= 100 (the 07-28
           IWM vol=0 contract produced an untrustworthy friction datapoint).
           Entry is recorded AT THE OBSERVED ASK, unconditionally — calibration
           measures machinery, not selectivity. Never overwrites an entry.
  EXIT   — >= 40 minutes after entry, or forced at the last pilot slot (11:23);
           recorded AT THE OBSERVED BID. P&L is adjudicated only by the
           deterministic close report, never here.

The snapshot's option slice carries one 7-21 DTE expiration (the collector's
window rule), which satisfies the DTE bound; the exact expiration used is
recorded. Every record carries CALIBRATION_EXCLUDED_FROM_PERFORMANCE and does
not consume the one-per-day policy-trade budget.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from research.trajectory_recorder import (
    _integer,
    _number,
    option_instruments,
    option_quotes_by_instrument,
)

EVIDENCE_CLASS = "CALIBRATION_EXCLUDED_FROM_PERFORMANCE"
DELTA_LOW, DELTA_HIGH, DELTA_TARGET = 0.30, 0.65, 0.50
PREMIUM_BANDS = (75.0, 120.0, 300.0)
LIQUIDITY_FLOOR = 100          # volume AND open_interest minimum
HOLD_MINUTES = 40
ENTRY_LAST_SLOT = (11, 3)      # no new entry after the 11:03 slot
FORCED_EXIT_SLOT = (11, 23)    # last pilot slot forces the exit


def ranked_symbols(decision: Mapping[str, Any]) -> list[str]:
    """Universe symbols by descending volume_ratio (no signal requirement)."""
    rows = []
    for symbol, report in (decision.get("symbols") or {}).items():
        ratio = report.get("volume_ratio") if isinstance(report, Mapping) else None
        if isinstance(ratio, (int, float)):
            rows.append((float(ratio), str(symbol)))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [symbol for _ratio, symbol in rows]


def select_calibration_contract(
    envelope: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any], int] | None:
    """(instrument, quote, premium_band) per the frozen rule, or None."""
    quotes = option_quotes_by_instrument(envelope)
    eligible = []
    for instrument in option_instruments(envelope):
        quote = quotes.get(str(instrument.get("id") or ""))
        if quote is None:
            continue
        delta = _number(quote.get("delta"))
        mark = _number(quote.get("adjusted_mark_price")) or _number(quote.get("mark_price"))
        volume = _integer(quote.get("volume")) or 0
        open_interest = _integer(quote.get("open_interest")) or 0
        if delta is None or mark is None:
            continue
        if not (DELTA_LOW <= abs(delta) <= DELTA_HIGH):
            continue
        if volume < LIQUIDITY_FLOOR or open_interest < LIQUIDITY_FLOOR:
            continue
        premium = mark * 100.0
        band = next((b for b in PREMIUM_BANDS if premium <= b), None)
        if band is None:
            continue
        eligible.append((band, abs(abs(delta) - DELTA_TARGET), -open_interest, instrument, quote))
    if not eligible:
        return None
    eligible.sort(key=lambda row: (row[0], row[1], row[2]))
    band, _d, _oi, instrument, quote = eligible[0]
    return instrument, quote, int(band)


def entry_record(
    *, run_id: str, symbol: str, instrument: Mapping[str, Any],
    quote: Mapping[str, Any], premium_band: int,
    observed_at: datetime, source_updated_at: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "symbol": symbol,
        "instrument_id": str(instrument.get("id") or ""),
        "strike": _number(instrument.get("strike_price")),
        "expiration_date": str(instrument.get("expiration_date") or "") or None,
        "option_type": str(instrument.get("type") or "").upper() or None,
        "delta": _number(quote.get("delta")),
        "implied_volatility": _number(quote.get("implied_volatility")),
        "volume": _integer(quote.get("volume")),
        "open_interest": _integer(quote.get("open_interest")),
        "premium_band": premium_band,
        "entry_observed_at": observed_at.isoformat(),
        "entry_bid": _number(quote.get("bid_price")),
        "entry_ask": _number(quote.get("ask_price")),
        "entry_mark": _number(quote.get("adjusted_mark_price")) or _number(quote.get("mark_price")),
        "source_updated_at": source_updated_at,
        "evidence_class": EVIDENCE_CLASS,
    }


def exit_record(
    *, run_id: str, entry: Mapping[str, Any], quote: Mapping[str, Any],
    observed_at: datetime, exit_reason: str,
) -> dict[str, Any]:
    entry_at = datetime.fromisoformat(str(entry["entry_observed_at"]))
    holding = (observed_at - entry_at).total_seconds() / 60.0
    return {
        "schema_version": 1,
        "run_id": run_id,
        "exit_observed_at": observed_at.isoformat(),
        "exit_bid": _number(quote.get("bid_price")),
        "exit_ask": _number(quote.get("ask_price")),
        "exit_mark": _number(quote.get("adjusted_mark_price")) or _number(quote.get("mark_price")),
        "holding_minutes": round(holding, 2),
        "exit_reason": exit_reason,
        "evidence_class": EVIDENCE_CLASS,
    }


def calibration_dir(project_root: Path, day: str) -> Path:
    return project_root / "logs/calibration" / day


def write_once(path: Path, payload: Mapping[str, Any]) -> bool:
    """Atomic create-if-absent; False when the file already exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return True


def load_entry(project_root: Path, day: str) -> dict[str, Any] | None:
    path = calibration_dir(project_root, day) / "entry.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def exit_due(entry: Mapping[str, Any], now: datetime, slot_hhmm: tuple[int, int]) -> str | None:
    """HORIZON_40_MIN / FORCED_LAST_PILOT_SLOT / None (not yet due)."""
    try:
        entry_at = datetime.fromisoformat(str(entry["entry_observed_at"]))
    except (KeyError, ValueError):
        return None
    if (now - entry_at).total_seconds() >= HOLD_MINUTES * 60:
        return "HORIZON_40_MIN"
    if slot_hhmm == FORCED_EXIT_SLOT:
        return "FORCED_LAST_PILOT_SLOT"
    return None


def entry_allowed(slot_hhmm: tuple[int, int]) -> bool:
    return slot_hhmm <= ENTRY_LAST_SLOT

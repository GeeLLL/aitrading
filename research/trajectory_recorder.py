"""Deterministic quote-trajectory recorder.

Until now, trajectory events (the raw material of the paired-label experiment)
were written by an LLM agent each slot, following prose instructions — meaning
the day's primary data product depended on a model improvising JSON. This
module replaces that with pure functions whose output satisfies both
``config/quote_trajectory.schema.json`` and the deterministic consumer
(``scripts/eod_report.reconstruct_trade``).

Event model (mirrors the reader):
  CANDIDATE      — opens a trajectory; records the quote at decision time; its
                   ask IS the entry limit; fill window = limit_recorded_at + 60s.
  QUOTE          — a later observation of the same instrument (fill evidence).
  HORIZON_CLOSE  — the closing observation at/after the holding horizon.

One event per file under logs/quote_trajectories/<PT-date>/; files are grouped
by trajectory_id downstream. Nothing here talks to the network: callers pass in
already-vaulted snapshot envelopes, so every recorded value traces to an
immutable, hashed source. Unknown fields stay null — never invented.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

EVIDENCE_CLASS = "PILOT_EXCLUDED_FROM_PERFORMANCE"
FILL_WINDOW_SECONDS = 60          # strategy_v1.0 maximum_fill_wait_seconds
TARGET_HORIZON_MINUTES = 60       # strategy_v1.0 maximum_holding_minutes

REQUIRED_FIELDS = (
    "schema_version", "trajectory_id", "event_type", "policy_labels",
    "instrument_id", "underlying", "option_type", "strike", "expiration_date",
    "decision_time", "quote_received_at", "source_updated_at",
    "bid", "ask", "mark", "volume", "open_interest", "evidence_class",
)


class TrajectoryError(ValueError):
    pass


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def tool_outputs(envelope: Mapping[str, Any], tool: str) -> list[Mapping[str, Any]]:
    """All non-degraded outputs of one tool from a vault snapshot envelope."""
    results = (envelope.get("response") or {}).get("tool_results") or []
    outputs = []
    for entry in results:
        if isinstance(entry, Mapping) and entry.get("tool") == tool:
            output = entry.get("output")
            if isinstance(output, Mapping):
                outputs.append(output)
    return outputs


def option_instruments(envelope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    instruments: list[Mapping[str, Any]] = []
    for output in tool_outputs(envelope, "get_option_instruments"):
        data = output.get("data") if isinstance(output.get("data"), Mapping) else {}
        instruments.extend(
            entry for entry in (data.get("instruments") or []) if isinstance(entry, Mapping)
        )
    return instruments


def option_quotes_by_instrument(envelope: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    quotes: dict[str, Mapping[str, Any]] = {}
    for output in tool_outputs(envelope, "get_option_quotes"):
        data = output.get("data") if isinstance(output.get("data"), Mapping) else {}
        for entry in data.get("results") or []:
            quote = entry.get("quote") if isinstance(entry, Mapping) else None
            if not isinstance(quote, Mapping):
                continue
            instrument_id = str(
                _first(quote, "instrument_id", "option_instrument_id", "id") or ""
            )
            if instrument_id:
                quotes[instrument_id] = quote
    return quotes


def underlying_last_trade(envelope: Mapping[str, Any], symbol: str) -> float | None:
    for output in tool_outputs(envelope, "get_equity_quotes"):
        data = output.get("data") if isinstance(output.get("data"), Mapping) else {}
        for entry in data.get("results") or []:
            if not isinstance(entry, Mapping):
                continue
            quote = entry.get("quote") if isinstance(entry.get("quote"), Mapping) else {}
            if quote.get("symbol") == symbol:
                return _number(_first(quote, "last_trade_price"))
    return None


def nearest_the_money(
    instruments: Iterable[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    underlying_price: float,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The quoted instrument whose strike is closest to the underlying.

    Only instruments that actually have a quote qualify — a candidate without an
    observable ask can never be adjudicated and would be dead weight.
    """
    best: tuple[float, Mapping[str, Any], Mapping[str, Any]] | None = None
    for instrument in instruments:
        strike = _number(instrument.get("strike_price"))
        instrument_id = str(instrument.get("id") or "")
        quote = quotes.get(instrument_id)
        if strike is None or quote is None or _number(_first(quote, "ask_price", "ask")) is None:
            continue
        distance = abs(strike - underlying_price)
        if best is None or distance < best[0]:
            best = (distance, instrument, quote)
    return (best[1], best[2]) if best else None


def _base_event(
    *,
    event_type: str,
    trajectory_id: str,
    instrument: Mapping[str, Any],
    quote: Mapping[str, Any],
    decision_time: datetime,
    quote_received_at: datetime,
    source_updated_at: str | None,
    policy_labels: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "trajectory_id": trajectory_id,
        "event_type": event_type,
        "policy_labels": policy_labels,
        "instrument_id": str(instrument.get("id") or ""),
        "underlying": str(_first(instrument, "chain_symbol", "underlying_symbol") or ""),
        "option_type": str(instrument.get("type") or "").upper() or None,
        "strike": _number(instrument.get("strike_price")),
        "expiration_date": str(instrument.get("expiration_date") or "") or None,
        "decision_time": decision_time.isoformat(),
        "quote_received_at": quote_received_at.isoformat(),
        "source_updated_at": source_updated_at,
        "bid": _number(_first(quote, "bid_price", "bid")),
        "ask": _number(_first(quote, "ask_price", "ask")),
        "mark": _number(_first(quote, "adjusted_mark_price", "mark_price", "mark")),
        "volume": _integer(quote.get("volume")),
        "open_interest": _integer(quote.get("open_interest")),
        "delta": _number(quote.get("delta")),
        "implied_volatility": _number(quote.get("implied_volatility")),
        "theta": _number(quote.get("theta")),
        "evidence_class": EVIDENCE_CLASS,
    }


def candidate_event(
    *,
    instrument: Mapping[str, Any],
    quote: Mapping[str, Any],
    decision_time: datetime,
    quote_received_at: datetime,
    source_updated_at: str | None,
    policy_labels: list[str],
    rejection_reasons: list[str] | None = None,
) -> dict[str, Any]:
    instrument_id = str(instrument.get("id") or "")
    if not instrument_id:
        raise TrajectoryError("CANDIDATE_WITHOUT_INSTRUMENT_ID")
    trajectory_id = f"{_first(instrument, 'chain_symbol') or 'UNKNOWN'}-{instrument_id[:8]}-{decision_time:%Y%m%dT%H%M%S}"
    event = _base_event(
        event_type="CANDIDATE",
        trajectory_id=trajectory_id,
        instrument=instrument,
        quote=quote,
        decision_time=decision_time,
        quote_received_at=quote_received_at,
        source_updated_at=source_updated_at,
        policy_labels=policy_labels,
    )
    event["rejection_reasons"] = list(rejection_reasons or [])
    event["target_horizon_minutes"] = TARGET_HORIZON_MINUTES
    # The recorded ask IS the entry limit (mirrors reconstruct_trade).
    event["limit_price"] = event["ask"]
    event["limit_recorded_at"] = quote_received_at.isoformat()
    event["fill_window_deadline"] = (
        quote_received_at + timedelta(seconds=FILL_WINDOW_SECONDS)
    ).isoformat()
    return event


def observation_event(
    *,
    event_type: str,   # QUOTE or HORIZON_CLOSE
    candidate: Mapping[str, Any],
    quote: Mapping[str, Any],
    quote_received_at: datetime,
    source_updated_at: str | None,
) -> dict[str, Any]:
    if event_type not in ("QUOTE", "HORIZON_CLOSE"):
        raise TrajectoryError(f"UNSUPPORTED_EVENT_TYPE:{event_type}")
    instrument = {
        "id": candidate.get("instrument_id"),
        "chain_symbol": candidate.get("underlying"),
        "type": candidate.get("option_type"),
        "strike_price": candidate.get("strike"),
        "expiration_date": candidate.get("expiration_date"),
    }
    decision_time = datetime.fromisoformat(str(candidate["decision_time"]))
    return _base_event(
        event_type=event_type,
        trajectory_id=str(candidate["trajectory_id"]),
        instrument=instrument,
        quote=quote,
        decision_time=decision_time,
        quote_received_at=quote_received_at,
        source_updated_at=source_updated_at,
        policy_labels=[str(label) for label in (candidate.get("policy_labels") or [])],
    )


def validate_event(event: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in event]
    if missing:
        raise TrajectoryError("EVENT_MISSING_FIELDS:" + ",".join(missing))
    if event.get("evidence_class") != EVIDENCE_CLASS:
        raise TrajectoryError("EVENT_WRONG_EVIDENCE_CLASS")


def write_event(trajectory_root: str | Path, event: Mapping[str, Any]) -> Path:
    validate_event(event)
    root = Path(trajectory_root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = str(event["quote_received_at"]).replace(":", "").replace("+", "p")
    path = root / f"{event['trajectory_id']}.{str(event['event_type']).lower()}.{stamp}.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(event), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load_day_events(trajectory_root: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Group the day's events by trajectory_id (reader-compatible; unreadable
    files are skipped — the EOD report warns about them separately)."""
    root = Path(trajectory_root)
    groups: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return groups
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("trajectory_id"):
            groups.setdefault(str(payload["trajectory_id"]), []).append(payload)
    return groups


def open_refresh_targets(
    groups: Mapping[str, list[Mapping[str, Any]]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Candidates still needing observation: inside the fill window, or past the
    horizon without a HORIZON_CLOSE yet. Never re-observes a closed trajectory."""
    targets: list[dict[str, Any]] = []
    for events in groups.values():
        candidate = next(
            (event for event in events if event.get("event_type") == "CANDIDATE"), None
        )
        if candidate is None or candidate.get("rejection_reasons"):
            continue
        has_close = any(event.get("event_type") == "HORIZON_CLOSE" for event in events)
        if has_close:
            continue
        try:
            decision_time = datetime.fromisoformat(str(candidate["decision_time"]))
        except (KeyError, ValueError):
            continue
        horizon_minutes = _integer(candidate.get("target_horizon_minutes")) or TARGET_HORIZON_MINUTES
        horizon_at = decision_time + timedelta(minutes=horizon_minutes)
        kind = "HORIZON_CLOSE" if now >= horizon_at else "QUOTE"
        targets.append({"candidate": dict(candidate), "event_type": kind})
    return targets

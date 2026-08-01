"""Run the frozen strategy over a vaulted snapshot, with no model in the loop.

This is the piece that moves the live decision from "the agent computed it and
told us" to "deterministic code computed it from hash-anchored data". It:

  1. derives each symbol's signal inputs from the snapshot's own bars
     (research.universe_features),
  2. runs the FROZEN ``strategy.market_regime.validate_bar_set`` over the
     reference bars, so bar-time integrity is enforced on the actual decision
     inputs rather than asserted about them,
  3. runs the FROZEN ``determine_market_regime`` and
     ``evaluate_underlying_signal`` — the same functions the test suite
     exercises — to produce the regime and per-symbol decisions.

Nothing here re-implements strategy logic; it only supplies inputs to it. Every
unknown stays None so the frozen evaluators fail closed on it.
"""

from __future__ import annotations

import json
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from execution.official_mcp_collector import _parse_iso_aware
from research.universe_features import (
    derive_features,
    load_signal_policy,
    parse_bars,
    rolling_indicator_bars,
)
from strategy.market_regime import determine_market_regime, validate_bar_set
from strategy.underlying_signal import (
    UnderlyingSignalSnapshot,
    evaluate_underlying_signal,
)

REFERENCE_SYMBOLS = ("SPY", "QQQ")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def evaluate_snapshot(
    snapshot_path: str | Path,
    *,
    project_root: Path,
    universe: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministically evaluate the frozen strategy over one vault snapshot."""
    path = Path(snapshot_path)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    received_at = _parse_iso_aware(str(envelope.get("received_at") or ""))
    if received_at is None:
        return {"schema_version": 1, "status": "FAIL",
                "reason": "NO_TRUSTED_RECEIPT_TIME", "symbols": {}}

    signal_policy = load_signal_policy(project_root)
    with (project_root / "strategy/strategy_v1.0.toml").open("rb") as handle:
        full_policy = tomllib.load(handle)
    integrity = full_policy.get("data_integrity", {})
    interval = int(signal_policy["bar_interval_minutes"])
    regime_policy = full_policy.get("market_regime", {})
    confirmation_bars = int(regime_policy.get("confirmation_completed_bars", 2))

    grouped = parse_bars(envelope)
    if universe is not None:
        wanted = {symbol.upper() for symbol in universe}
        grouped = {s: b for s, b in grouped.items() if s in wanted}

    # 2. Frozen bar-time validation on the ACTUAL decision inputs.
    reference_bars = []
    for symbol in REFERENCE_SYMBOLS:
        reference_bars.extend(rolling_indicator_bars(
            grouped.get(symbol, []), received_at=received_at,
            interval_minutes=interval, count=confirmation_bars,
        ))
    bar_violations = validate_bar_set(
        reference_bars,
        decision_time=received_at,
        expected_interval_minutes=interval,
        maximum_receipt_delay_seconds=int(
            integrity.get("maximum_bar_receipt_delay_seconds", 10)
        ),
        maximum_latest_bar_lag_seconds=int(
            integrity.get("maximum_latest_completed_bar_lag_seconds", 420)
        ),
    )

    # 3. Frozen regime, then the frozen per-symbol signal.
    regime = determine_market_regime(
        reference_bars,
        reference_symbols=REFERENCE_SYMBOLS,
        confirmation_bars=confirmation_bars,
    )

    minimum_volume_ratio = Decimal(str(signal_policy["minimum_volume_ratio"]))
    symbols: dict[str, Any] = {}
    for symbol, bars in sorted(grouped.items()):
        features = derive_features(bars, signal_policy)
        snapshot = UnderlyingSignalSnapshot(
            symbol=symbol,
            close=features["close"],
            vwap=features["vwap"],
            ema_fast=features["ema_fast"],
            ema_slow=features["ema_slow"],
            breakout_high=features["breakout_high"],
            breakdown_low=features["breakdown_low"],
            current_volume=features["current_volume"],
            average_volume=features["average_volume"],
        )
        decision = evaluate_underlying_signal(
            snapshot, regime.regime, minimum_volume_ratio=minimum_volume_ratio,
        )
        symbols[symbol] = {
            "features": _jsonable(features),
            "direction": decision.direction.value,
            "reasons": list(decision.reasons),
            "volume_ratio": _jsonable(decision.volume_ratio),
        }

    signalled = sorted(
        symbol for symbol, row in symbols.items() if row["direction"] != "NO_TRADE"
    )
    # FAIL CLOSED on bar-time integrity. Demonstrated live on 2026-07-30: a gate
    # snapshot carrying the PRIOR session's bars (whose closing bar holds the
    # end-of-day volume spike) made the frozen signal report SPY and QQQ as
    # qualified PUTs at volume_ratio 4.4-4.7. Those are artefacts of stale data,
    # not signals. A signal derived from bars the frozen validator rejects must
    # never be admissible, however clean the signal itself looks.
    admissible = not bar_violations
    qualified = signalled if admissible else []
    return {
        "schema_version": 1,
        "status": "OK",
        "provenance": "HARVESTED_VAULT_SNAPSHOT_FROZEN_STRATEGY_CODE",
        "snapshot_path": str(path),
        "snapshot_id": envelope.get("snapshot_id"),
        "received_at": received_at.isoformat(),
        "minimum_volume_ratio": float(minimum_volume_ratio),
        "bar_time_violations": list(bar_violations),
        "bar_time_sound": not bar_violations,
        "regime": regime.regime.value,
        "regime_reasons": list(regime.reasons),
        "symbols": symbols,
        "decision_admissible": admissible,
        "inadmissible_reason": None if admissible else "BAR_TIME_INTEGRITY_VIOLATED",
        "signalled_symbols": signalled,
        "qualified_symbols": qualified,
        "note": (
            "Computed by strategy.market_regime and strategy.underlying_signal "
            "from the snapshot's own bars. No model produced any number here. "
            "qualified_symbols is empty whenever bar-time integrity fails: a "
            "signal computed from rejected bars is an artefact, not a signal."
        ),
    }

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
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from collections.abc import Sequence
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


def _merge_bars(grouped: dict[str, list], addition: dict[str, list]) -> None:
    """Fold one snapshot's bars into the accumulator, de-duplicated by bar time."""
    for symbol, bars in addition.items():
        existing = grouped.setdefault(symbol, [])
        seen = {b.begins_at for b in existing}
        existing.extend(b for b in bars if b.begins_at not in seen)
        existing.sort(key=lambda b: b.begins_at)


def evaluate_snapshot(
    snapshot_path: str | Path | Sequence[str | Path],
    *,
    project_root: Path,
    universe: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministically evaluate the frozen strategy over one or more snapshots.

    The universe does not fit in a single bars probe (see
    ``BARS_PROBE_CHUNK_SYMBOLS``), so a slot vaults several. They are evaluated
    together, and freshness is judged against the OLDEST receipt time in the
    set — the most conservative choice, so chunking can never make bar-time
    integrity look better than the slowest chunk actually was.
    """
    if isinstance(snapshot_path, (str, Path)):
        paths = [Path(snapshot_path)]
    else:
        paths = [Path(item) for item in snapshot_path]
    if not paths:
        return {"schema_version": 1, "status": "FAIL",
                "reason": "NO_SNAPSHOTS", "symbols": {}}
    path = paths[0]
    envelopes = []
    receipts = []
    for candidate in paths:
        envelope = json.loads(candidate.read_text(encoding="utf-8"))
        stamp = _parse_iso_aware(str(envelope.get("received_at") or ""))
        if stamp is None:
            return {"schema_version": 1, "status": "FAIL",
                    "reason": "NO_TRUSTED_RECEIPT_TIME", "symbols": {}}
        envelopes.append(envelope)
        receipts.append(stamp)
    envelope = envelopes[0]
    received_at = min(receipts)

    signal_policy = load_signal_policy(project_root)
    with (project_root / "strategy/strategy_v1.0.toml").open("rb") as handle:
        full_policy = tomllib.load(handle)
    integrity = full_policy.get("data_integrity", {})
    interval = int(signal_policy["bar_interval_minutes"])
    regime_policy = full_policy.get("market_regime", {})
    confirmation_bars = int(regime_policy.get("confirmation_completed_bars", 2))

    grouped: dict[str, list] = {}
    for item in envelopes:
        _merge_bars(grouped, parse_bars(item))
    # The venue pre-populates the WHOLE regular session as a grid, so a probe run
    # at 16:23Z comes back carrying zero-volume placeholder bars stamped out to
    # the 20:00Z close. Those are not data — they are rows for minutes that have
    # not happened. Left in, they made the frozen validator report
    # SPY_BAR_FROM_FUTURE and rendered every slot inadmissible on 2026-08-03.
    # Drop bars that have not begun as of the receipt; keep everything else, so
    # a genuinely stale or out-of-order bar still trips the integrity check.
    #
    # The boundary is the bar's END, not its start: a bar that began at 16:20 is
    # still forming at a 16:23 receipt, and the frozen strategy is defined on
    # COMPLETED bars throughout (it is also why the volume average excludes the
    # newest bar — an unconsolidated bar can even report decreasing volume
    # between reads). Anything whose close has not happened yet is dropped.
    horizon = received_at - timedelta(minutes=interval)
    grouped = {
        symbol: [bar for bar in bars if bar.begins_at <= horizon]
        for symbol, bars in grouped.items()
    }
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
    # validate_bar_set finds nothing wrong with an EMPTY set, so an empty one
    # would sail through as "sound" and make the slot admissible on no data at
    # all. Absence of the reference bars is itself a violation.
    missing_reference = [
        symbol for symbol in REFERENCE_SYMBOLS
        if len([b for b in reference_bars if b.symbol == symbol]) < confirmation_bars
    ]
    bar_violations = [f"{symbol}_REFERENCE_BARS_MISSING" for symbol in missing_reference]
    bar_violations += validate_bar_set(
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
        "snapshot_paths": [str(item) for item in paths],
        "snapshot_ids": [item.get("snapshot_id") for item in envelopes],
        "received_at": received_at.isoformat(),
        "receipt_times": sorted(stamp.isoformat() for stamp in receipts),
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

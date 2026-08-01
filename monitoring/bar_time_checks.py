"""Deterministic bar-time verification straight from a vault snapshot.

``strategy/market_regime.validate_bar_set`` already encodes the right rules,
but it only runs inside ShadowRunner — a path the live launchd slots never
take. In production the freshness numbers in each slot's summary are therefore
*model-authored assertions*, which is exactly the weak tier this project tries
to eliminate everywhere else.

This module closes that gap from the other end: it re-derives bar-time
integrity from the immutable ``get_equity_historicals`` payload inside a raw
snapshot, with no model involvement. It answers, per symbol:

  * are the bars strictly ordered, with no duplicates?
  * is the interval uniform (no silently-missing or double-length bars)?
  * does any bar begin at or after the snapshot's own receipt time (future data)?
  * is the newest completed bar within the configured lag limit?

Every irregularity is reported by name; nothing is repaired and nothing is
inferred. Unknown or unparsable input fails closed as an irregularity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from execution.official_mcp_collector import _parse_iso_aware
from execution.raw_data_vault import RawDataVault
from monitoring.daily_schedule import SESSION_TIMEZONE
from monitoring.market_calendar import is_market_open

# Regular session in the session timezone. Outside it, stale bars are CORRECT —
# the raw collector deliberately returns the latest available session when the
# market is closed — so the newest-bar lag rule must not fire then.
_SESSION_OPEN_HOUR_MINUTE = (6, 30)
_SESSION_CLOSE_HOUR_MINUTE = (13, 0)


def within_regular_session(moment: datetime) -> bool:
    local = moment.astimezone(SESSION_TIMEZONE)
    if not is_market_open(local.date()):
        return False
    open_at = local.replace(hour=_SESSION_OPEN_HOUR_MINUTE[0], minute=_SESSION_OPEN_HOUR_MINUTE[1],
                            second=0, microsecond=0)
    close_at = local.replace(hour=_SESSION_CLOSE_HOUR_MINUTE[0], minute=_SESSION_CLOSE_HOUR_MINUTE[1],
                             second=0, microsecond=0)
    return open_at <= local <= close_at

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_MAX_NEWEST_BAR_LAG_SECONDS = 420


@dataclass(frozen=True)
class SymbolBarReport:
    symbol: str
    bar_count: int
    newest_bar_begins_at: str | None
    newest_bar_lag_seconds: float | None
    irregularities: tuple[str, ...]

    @property
    def sound(self) -> bool:
        return not self.irregularities

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bar_count": self.bar_count,
            "newest_bar_begins_at": self.newest_bar_begins_at,
            "newest_bar_lag_seconds": self.newest_bar_lag_seconds,
            "irregularities": list(self.irregularities),
            "sound": self.sound,
        }


def _bars_by_symbol(envelope: Mapping[str, Any]) -> dict[str, list[dict]]:
    """Extract bar lists per symbol from every get_equity_historicals result."""
    grouped: dict[str, list[dict]] = {}
    response = envelope.get("response")
    results = response.get("tool_results") if isinstance(response, Mapping) else None
    if not isinstance(results, list):
        return grouped
    for result in results:
        if not isinstance(result, Mapping) or result.get("tool") != "get_equity_historicals":
            continue
        output = result.get("output")
        data = output.get("data", output) if isinstance(output, Mapping) else output
        rows = data.get("results") if isinstance(data, Mapping) else data
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            bars = row.get("bars")
            if not isinstance(bars, list):
                continue
            symbol = str(row.get("symbol") or row.get("chain_symbol") or "")
            if not symbol:
                # Fall back to the first bar's own symbol field when present.
                first = bars[0] if bars and isinstance(bars[0], Mapping) else {}
                symbol = str(first.get("symbol") or "UNKNOWN")
            grouped.setdefault(symbol, []).extend(
                bar for bar in bars if isinstance(bar, Mapping)
            )
    return grouped


def verify_symbol_bars(
    symbol: str,
    bars: list[dict],
    *,
    received_at: datetime,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    maximum_newest_bar_lag_seconds: int = DEFAULT_MAX_NEWEST_BAR_LAG_SECONDS,
    enforce_freshness: bool = True,
) -> SymbolBarReport:
    """Pure verification of one symbol's bar series. Never repairs anything.

    ``enforce_freshness`` gates ONLY the newest-bar lag rule: ordering,
    duplicates, interval uniformity and future-bar rejection always apply.
    """
    irregularities: list[str] = []
    if not bars:
        return SymbolBarReport(symbol, 0, None, None, ("NO_BARS",))

    stamps: list[datetime] = []
    for bar in bars:
        parsed = _parse_iso_aware(str(bar.get("begins_at") or ""))
        if parsed is None:
            irregularities.append("BAR_TIMESTAMP_UNPARSABLE")
            continue
        stamps.append(parsed)
    if not stamps:
        return SymbolBarReport(symbol, len(bars), None, None, tuple(irregularities) or ("NO_BARS",))

    ordered = sorted(stamps)
    if stamps != ordered:
        irregularities.append("BARS_OUT_OF_ORDER")
    if len(set(stamps)) != len(stamps):
        irregularities.append("DUPLICATE_BARS")

    interval = timedelta(seconds=interval_seconds)
    for earlier, later in zip(ordered, ordered[1:]):
        gap = later - earlier
        if gap != interval:
            irregularities.append("NON_UNIFORM_BAR_INTERVAL")
            break

    newest = ordered[-1]
    # A bar that BEGINS at or after our own receipt time cannot have completed.
    if newest >= received_at:
        irregularities.append("BAR_FROM_FUTURE")
    # Lag is measured from the newest bar's completion, not its start.
    lag = (received_at - (newest + interval)).total_seconds()
    if enforce_freshness and lag > maximum_newest_bar_lag_seconds:
        # Name the two cases apart: bars from an earlier session are a
        # different fact from bars that merely arrived late, and conflating
        # them makes the report useless for diagnosis.
        same_session = (
            newest.astimezone(SESSION_TIMEZONE).date()
            == received_at.astimezone(SESSION_TIMEZONE).date()
        )
        irregularities.append(
            "NEWEST_COMPLETED_BAR_STALE" if same_session else "BARS_FROM_PRIOR_SESSION"
        )

    return SymbolBarReport(
        symbol=symbol,
        bar_count=len(bars),
        newest_bar_begins_at=newest.isoformat(),
        newest_bar_lag_seconds=round(lag, 3),
        irregularities=tuple(dict.fromkeys(irregularities)),
    )


def verify_snapshot_bar_times(
    snapshot_path: str | Path,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    maximum_newest_bar_lag_seconds: int = DEFAULT_MAX_NEWEST_BAR_LAG_SECONDS,
    require_indexed: bool = True,
) -> dict[str, Any]:
    """Adjudicate bar-time integrity for every symbol in a vault snapshot."""
    path = Path(snapshot_path)
    try:
        receipt = RawDataVault.verify(path, require_indexed=require_indexed)
    except (OSError, ValueError) as error:
        return {
            "schema_version": 1,
            "status": "FAIL",
            "reason": f"SNAPSHOT_VERIFY_FAILED:{error}",
            "symbols": [],
        }
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "schema_version": 1,
            "status": "FAIL",
            "reason": f"SNAPSHOT_UNREADABLE:{error}",
            "symbols": [],
        }
    received_value = envelope.get("received_at")
    received = _parse_iso_aware(str(received_value)) if isinstance(received_value, str) else None
    if received is None:
        return {
            "schema_version": 1,
            "status": "FAIL",
            "reason": "NO_TRUSTED_RECEIPT_TIME",
            "symbols": [],
        }

    enforce_freshness = within_regular_session(received)
    grouped = _bars_by_symbol(envelope)
    if not grouped:
        return {
            "schema_version": 1,
            "status": "FAIL",
            "reason": "NO_HISTORICAL_BARS_IN_SNAPSHOT",
            "snapshot_id": receipt.snapshot_id,
            "symbols": [],
        }
    reports = [
        verify_symbol_bars(
            symbol, bars,
            received_at=received,
            interval_seconds=interval_seconds,
            maximum_newest_bar_lag_seconds=maximum_newest_bar_lag_seconds,
            enforce_freshness=enforce_freshness,
        )
        for symbol, bars in sorted(grouped.items())
    ]
    unsound = [report for report in reports if not report.sound]
    return {
        "schema_version": 1,
        "status": "PASS" if not unsound else "FAIL",
        "reason": None if not unsound else "BAR_TIME_IRREGULARITIES",
        "snapshot_id": receipt.snapshot_id,
        "snapshot_sha256": receipt.content_sha256,
        "received_at": received.isoformat(),
        "interval_seconds": interval_seconds,
        "maximum_newest_bar_lag_seconds": maximum_newest_bar_lag_seconds,
        "freshness_enforced": enforce_freshness,
        "symbols": [report.to_dict() for report in reports],
        "provenance": "HARVESTED_VAULT_SNAPSHOT",
    }

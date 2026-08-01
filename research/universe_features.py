"""Derive the frozen strategy's inputs from vaulted bars, without a model.

Today the live pilot agent fetches bars into its own context, computes
volume_ratio / VWAP / EMA / breakout levels in scripts it writes itself, and
reports the answers. Those numbers are therefore model-authored assertions: no
deterministic code ever re-derives them, which is exactly the weak evidence
tier this project eliminates everywhere else.

This module computes those inputs from the immutable ``get_equity_historicals``
payload of a vault snapshot, using the parameters the frozen policy already
pins (``strategy/strategy_v1.0.toml`` [underlying_signal]). Feeding the result
into ``strategy.underlying_signal.evaluate_underlying_signal`` and
``strategy.market_regime.determine_market_regime`` means the live decision runs
on the same frozen code the tests exercise, over data that is hash-anchored in
the vault.

Definitions used (all from completed bars only, newest bar last):
  * current_volume  — the newest completed bar's volume
  * average_volume  — mean volume of the ``volume_average_lookback_bars``
                      completed bars BEFORE the newest one. The newest bar is
                      excluded from its own average, otherwise a volume spike
                      partially cancels itself.
  * vwap            — session VWAP, sum(typical*volume)/sum(volume) over the
                      newest bar's own session date
  * ema_fast/slow   — 9/20-period EMA of closes, seeded with an SMA
  * breakout_high   — highest HIGH of the ``breakout_lookback_completed_bars``
                      bars before the newest
  * breakdown_low   — lowest LOW of the same window

Anything that cannot be computed is left None so the frozen evaluator fails
closed on it; nothing is estimated or filled in.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from execution.official_mcp_collector import _parse_iso_aware

EMA_FAST_PERIODS = 9
EMA_SLOW_PERIODS = 20


@dataclass(frozen=True)
class Bar:
    symbol: str
    begins_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    session: str | None


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None


def parse_bars(envelope: Mapping[str, Any]) -> dict[str, list[Bar]]:
    """Extract per-symbol completed regular-session bars, ordered oldest first.

    Only ``session == "reg"`` bars are kept: extended-hours prints are not part
    of the frozen five-minute regular-session setup, and mixing them would
    silently change every derived value.
    """
    grouped: dict[str, list[Bar]] = {}
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
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            parsed: list[Bar] = []
            for raw in bars:
                if not isinstance(raw, Mapping):
                    continue
                session = raw.get("session")
                if session is not None and str(session) != "reg":
                    continue
                begins = _parse_iso_aware(str(raw.get("begins_at") or ""))
                close = _decimal(raw.get("close_price"))
                high = _decimal(raw.get("high_price"))
                low = _decimal(raw.get("low_price"))
                opened = _decimal(raw.get("open_price"))
                volume = raw.get("volume")
                if begins is None or close is None or high is None or low is None:
                    continue
                if opened is None or not isinstance(volume, int):
                    continue
                parsed.append(Bar(symbol, begins, opened, high, low, close, volume,
                                  None if session is None else str(session)))
            if parsed:
                parsed.sort(key=lambda bar: bar.begins_at)
                grouped.setdefault(symbol, []).extend(parsed)
    for symbol, bars in grouped.items():
        bars.sort(key=lambda bar: bar.begins_at)
    return grouped


def exponential_moving_average(closes: list[Decimal], periods: int) -> Decimal | None:
    """SMA-seeded EMA of the closes. None when there is not enough history."""
    if periods <= 0 or len(closes) < periods:
        return None
    seed = sum(closes[:periods]) / Decimal(periods)
    multiplier = Decimal(2) / Decimal(periods + 1)
    ema = seed
    for close in closes[periods:]:
        ema = (close - ema) * multiplier + ema
    return ema


def session_vwap(bars: list[Bar]) -> Decimal | None:
    """Session VWAP over the newest bar's own session date."""
    if not bars:
        return None
    session_date = bars[-1].begins_at.date()
    same_day = [bar for bar in bars if bar.begins_at.date() == session_date]
    total_volume = sum(bar.volume for bar in same_day)
    if total_volume <= 0:
        return None
    weighted = sum(
        ((bar.high + bar.low + bar.close) / Decimal(3)) * Decimal(bar.volume)
        for bar in same_day
    )
    return weighted / Decimal(total_volume)


def derive_features(bars: list[Bar], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the frozen signal's inputs for one symbol. Never estimates."""
    lookback = int(policy["breakout_lookback_completed_bars"])
    volume_lookback = int(policy["volume_average_lookback_bars"])

    features: dict[str, Any] = {
        "bar_count": len(bars),
        "close": None, "vwap": None, "ema_fast": None, "ema_slow": None,
        "breakout_high": None, "breakdown_low": None,
        "current_volume": None, "average_volume": None,
        "volume_ratio": None, "newest_bar_begins_at": None,
        "insufficient": [],
    }
    if not bars:
        features["insufficient"].append("NO_BARS")
        return features

    newest = bars[-1]
    prior = bars[:-1]
    features["close"] = newest.close
    features["current_volume"] = newest.volume
    features["newest_bar_begins_at"] = newest.begins_at.isoformat()

    if len(prior) < lookback:
        features["insufficient"].append("INSUFFICIENT_BREAKOUT_LOOKBACK")
    else:
        window = prior[-lookback:]
        features["breakout_high"] = max(bar.high for bar in window)
        features["breakdown_low"] = min(bar.low for bar in window)

    if len(prior) < volume_lookback:
        features["insufficient"].append("INSUFFICIENT_VOLUME_LOOKBACK")
    else:
        window = prior[-volume_lookback:]
        average = Decimal(sum(bar.volume for bar in window)) / Decimal(len(window))
        features["average_volume"] = average
        if average > 0:
            features["volume_ratio"] = Decimal(newest.volume) / average

    closes = [bar.close for bar in bars]
    features["ema_fast"] = exponential_moving_average(closes, EMA_FAST_PERIODS)
    features["ema_slow"] = exponential_moving_average(closes, EMA_SLOW_PERIODS)
    if features["ema_fast"] is None or features["ema_slow"] is None:
        features["insufficient"].append("INSUFFICIENT_EMA_HISTORY")

    features["vwap"] = session_vwap(bars)
    if features["vwap"] is None:
        features["insufficient"].append("NO_SESSION_VWAP")
    return features


def load_signal_policy(project_root: Path) -> Mapping[str, Any]:
    with (project_root / "strategy/strategy_v1.0.toml").open("rb") as handle:
        return tomllib.load(handle)["underlying_signal"]


def features_from_snapshot(
    snapshot_path: str | Path, *, project_root: Path,
) -> tuple[dict[str, dict[str, Any]], Mapping[str, Any]]:
    """Per-symbol frozen-signal inputs derived from one vault snapshot."""
    envelope = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    policy = load_signal_policy(project_root)
    grouped = parse_bars(envelope)
    return (
        {symbol: derive_features(bars, policy) for symbol, bars in sorted(grouped.items())},
        policy,
    )


def rolling_indicator_bars(
    bars: list[Bar],
    *,
    received_at: datetime,
    interval_minutes: int,
    count: int,
) -> list[Any]:
    """Build the newest ``count`` bars as CompletedMarketBar with rolling values.

    ``determine_market_regime`` compares close/VWAP and fast/slow EMA on each of
    the confirmation bars, so each bar needs the indicator values as they stood
    AT that bar — not the newest values repeated. Provenance fields are filled
    from the bar's own timestamps and the snapshot's receipt time so the frozen
    ``validate_bar_set`` can check them; nothing is invented.
    """
    from datetime import timedelta

    from strategy.market_regime import CompletedMarketBar

    built: list[CompletedMarketBar] = []
    if count <= 0:
        return built
    start_index = max(0, len(bars) - count)
    for index in range(start_index, len(bars)):
        window = bars[: index + 1]
        closes = [bar.close for bar in window]
        bar = bars[index]
        built.append(CompletedMarketBar(
            symbol=bar.symbol,
            close=bar.close,
            vwap=session_vwap(window),
            ema_fast=exponential_moving_average(closes, EMA_FAST_PERIODS),
            ema_slow=exponential_moving_average(closes, EMA_SLOW_PERIODS),
            interval_minutes=interval_minutes,
            started_at=bar.begins_at,
            ended_at=bar.begins_at + timedelta(minutes=interval_minutes),
            source_updated_at=None,
            received_at=received_at,
            completed=True,
        ))
    return built

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Iterable
from zoneinfo import ZoneInfo

from strategy.deterministic_features import RawOhlcvBar, build_local_features
from strategy.market_regime import MarketRegime, determine_market_regime
from strategy.underlying_signal import SignalDirection, evaluate_underlying_signal


EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Candidate:
    timestamp: datetime
    symbol: str
    direction: str
    entry: Decimal
    exit: Decimal
    gross_return: Decimal
    net_return: Decimal
    volume_ratio: Decimal


@dataclass(frozen=True)
class GroupSummary:
    observations: int
    trades: int
    no_trade_rate: float
    direction_accuracy: float | None
    mean_net_return: float | None
    median_net_return: float | None
    cumulative_net_return: float
    maximum_drawdown: float


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("HISTORICAL_TIMESTAMP_NOT_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def load_symbol(path: Path) -> tuple[RawOhlcvBar, ...]:
    payload = json.loads(path.read_text())
    symbol = str(payload["symbol"]).upper()
    received = datetime.now(timezone.utc)
    result: list[RawOhlcvBar] = []
    for raw in payload["bars"]:
        start = _parse_time(raw["begins_at"])
        result.append(RawOhlcvBar(
            symbol=symbol,
            started_at=start,
            ended_at=start + timedelta(minutes=5),
            open=Decimal(str(raw["open"])),
            high=Decimal(str(raw["high"])),
            low=Decimal(str(raw["low"])),
            close=Decimal(str(raw["close"])),
            volume=int(Decimal(str(raw["volume"]))),
            source_updated_at=start + timedelta(minutes=5),
            received_at=received,
            completed=True,
        ))
    return tuple(sorted(result, key=lambda bar: bar.started_at))


def _regular_session(bar: RawOhlcvBar) -> bool:
    local = bar.started_at.astimezone(EASTERN)
    minutes = local.hour * 60 + local.minute
    return local.weekday() < 5 and 570 <= minutes < 960


def _index(bars: Iterable[RawOhlcvBar]) -> dict[datetime, RawOhlcvBar]:
    return {bar.started_at: bar for bar in bars if _regular_session(bar)}


def _feature_history(index: dict[datetime, RawOhlcvBar], now: datetime) -> tuple[RawOhlcvBar, ...]:
    local_date = now.astimezone(EASTERN).date()
    return tuple(bar for stamp, bar in sorted(index.items())
                 if stamp <= now and stamp.astimezone(EASTERN).date() == local_date)


def _forward_exit(index: dict[datetime, RawOhlcvBar], now: datetime, horizon: int) -> RawOhlcvBar | None:
    return index.get(now + timedelta(minutes=horizon))


def generate_candidates(
    data: dict[str, tuple[RawOhlcvBar, ...]],
    *,
    symbols: tuple[str, ...],
    references: tuple[str, ...] = ("SPY", "QQQ"),
    horizon_minutes: int = 30,
    friction_bps_round_trip: Decimal = Decimal("10"),
) -> tuple[Candidate, ...]:
    indexes = {symbol: _index(bars) for symbol, bars in data.items()}
    common = sorted(set(indexes[references[0]]) & set(indexes[references[1]]))
    output: list[Candidate] = []
    friction = friction_bps_round_trip / Decimal("10000")
    for now in common:
        local = now.astimezone(EASTERN)
        minutes = local.hour * 60 + local.minute
        if minutes < 600 or minutes >= 870:  # 10:00 through 14:25 ET
            continue
        reference_completed = []
        reference_ok = True
        for ref in references:
            history = _feature_history(indexes[ref], now)
            if len(history) < 20:
                reference_ok = False
                break
            completed, _ = build_local_features(history)
            reference_completed.extend(completed)
        if not reference_ok:
            continue
        regime = determine_market_regime(reference_completed).regime
        if regime is MarketRegime.NO_TRADE:
            continue
        for symbol in symbols:
            if symbol not in indexes or now not in indexes[symbol]:
                continue
            history = _feature_history(indexes[symbol], now)
            if len(history) < 20:
                continue
            _, snapshot = build_local_features(history)
            decision = evaluate_underlying_signal(snapshot, regime)
            if decision.direction is SignalDirection.NO_TRADE or decision.volume_ratio is None:
                continue
            exit_bar = _forward_exit(indexes[symbol], now, horizon_minutes)
            if exit_bar is None:
                continue
            entry = history[-1].close
            exit_price = exit_bar.close
            signed = (exit_price / entry - Decimal("1"))
            if decision.direction is SignalDirection.PUT:
                signed = -signed
            output.append(Candidate(
                now, symbol, decision.direction.value, entry, exit_price,
                signed, signed - friction, decision.volume_ratio,
            ))
    return tuple(output)


def select_one_per_day(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    selected: dict[object, Candidate] = {}
    for item in sorted(candidates, key=lambda x: (x.timestamp, x.symbol)):
        day = item.timestamp.astimezone(EASTERN).date()
        if day not in selected:
            selected[day] = item
    return tuple(selected[key] for key in sorted(selected))


def summarize(observations: int, trades: Iterable[Candidate]) -> GroupSummary:
    values = [float(item.net_return) for item in trades]
    correct = [item.gross_return > 0 for item in trades]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    count = len(values)
    return GroupSummary(
        observations, count, 1 - count / observations if observations else 1.0,
        sum(correct) / count if count else None,
        mean(values) if values else None,
        median(values) if values else None,
        equity, drawdown,
    )


def random_baseline(candidates: tuple[Candidate, ...], repetitions: int = 1000) -> dict[str, float | int | None]:
    if not candidates:
        return {"repetitions": repetitions, "mean_return": None, "p05_return": None,
                "p95_return": None, "probability_beating_zero": None}
    entries = select_one_per_day(candidates)
    rng = random.Random(20260717)
    totals: list[float] = []
    for _ in range(repetitions):
        total = 0.0
        for item in entries:
            friction = float(item.gross_return - item.net_return)
            random_direction_return = float(item.gross_return) * rng.choice((-1, 1))
            total += random_direction_return - friction
        totals.append(total)
    ordered = sorted(totals)
    return {
        "repetitions": repetitions,
        "mean_return": mean(totals),
        "p05_return": ordered[int(0.05 * (len(ordered) - 1))],
        "p95_return": ordered[int(0.95 * (len(ordered) - 1))],
        "probability_beating_zero": sum(value > 0 for value in totals) / len(totals),
    }


def select_ai_accepted(candidates: Iterable[Candidate], score_payload: dict) -> tuple[Candidate, ...]:
    ordered = tuple(sorted(candidates, key=lambda x: (x.timestamp, x.symbol)))
    scores = score_payload.get("scores", [])
    expected = {f"C{index + 1:04d}" for index in range(len(ordered))}
    observed = {row.get("candidate_id") for row in scores}
    if observed != expected or len(scores) != len(expected):
        raise ValueError("AI_SCORE_CARD_COVERAGE_INVALID")
    accepted = {row["candidate_id"] for row in scores
                if row["accept"] is True and int(row["confidence"]) >= 70}
    eligible = [row for index, row in enumerate(ordered)
                if f"C{index + 1:04d}" in accepted]
    return select_one_per_day(eligible)


def _within_days(rows: Iterable[Candidate], days: set[object]) -> tuple[Candidate, ...]:
    return tuple(row for row in rows if row.timestamp.astimezone(EASTERN).date() in days)


def export_blind_ai_cards(candidates: Iterable[Candidate], path: Path) -> None:
    """Export point-in-time inputs without any forward price or outcome fields."""
    cards = []
    for index, row in enumerate(sorted(candidates, key=lambda x: (x.timestamp, x.symbol))):
        cards.append({
            "candidate_id": f"C{index + 1:04d}",
            "timestamp": row.timestamp.isoformat(),
            "symbol": row.symbol,
            "mechanical_direction": row.direction,
            "volume_ratio": str(row.volume_ratio),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "instructions": "Score each card independently using only fields on that card; outcomes are hidden.",
        "cards": cards,
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Point-in-time 30-day underlying proxy experiment")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--cards-output", type=Path)
    parser.add_argument("--ai-scores", type=Path)
    args = parser.parse_args()
    paths = sorted(args.data_dir.glob("*.json"))
    data = {path.stem.upper(): load_symbol(path) for path in paths}
    required = set(args.symbols) | {"SPY", "QQQ"}
    missing = sorted(required - set(data))
    if missing:
        raise SystemExit(f"HISTORICAL_DATA_MISSING: {','.join(missing)}")
    candidates = generate_candidates(data, symbols=tuple(args.symbols))
    selected = select_one_per_day(candidates)
    if args.cards_output:
        export_blind_ai_cards(candidates, args.cards_output)
    session_days = sorted({bar.started_at.astimezone(EASTERN).date()
                           for bar in data["SPY"] if _regular_session(bar)})
    calibration_days = set(session_days[:5])
    validation_days = set(session_days[5:15])
    out_of_sample_days = set(session_days[15:])
    report = {
        "method": "UNDERLYING_DIRECTION_PROXY_NOT_OPTION_PNL",
        "data_files": len(paths),
        "candidate_events": len(candidates),
        "available_sessions": len(session_days),
        "requested_sessions": 30,
        "data_shortfall_sessions": max(0, 30 - len(session_days)),
        "deterministic": asdict(summarize(len(session_days), selected)),
        "time_split": {
            "calibration": asdict(summarize(len(calibration_days), _within_days(selected, calibration_days))),
            "validation": asdict(summarize(len(validation_days), _within_days(selected, validation_days))),
            "out_of_sample": asdict(summarize(len(out_of_sample_days), _within_days(selected, out_of_sample_days))),
        },
        "random_eligible_baseline": random_baseline(candidates),
        "selected": [{**asdict(x), "timestamp": x.timestamp.isoformat(),
                       "entry": str(x.entry), "exit": str(x.exit),
                       "gross_return": str(x.gross_return), "net_return": str(x.net_return),
                       "volume_ratio": str(x.volume_ratio)} for x in selected],
    }
    if args.ai_scores:
        ai_selected = select_ai_accepted(candidates, json.loads(args.ai_scores.read_text()))
        report["ai_blind_filter"] = asdict(summarize(len(session_days), ai_selected))
        report["ai_time_split"] = {
            "calibration": asdict(summarize(len(calibration_days), _within_days(ai_selected, calibration_days))),
            "validation": asdict(summarize(len(validation_days), _within_days(ai_selected, validation_days))),
            "out_of_sample": asdict(summarize(len(out_of_sample_days), _within_days(ai_selected, out_of_sample_days))),
        }
        report["ai_selected"] = [{**asdict(x), "timestamp": x.timestamp.isoformat(),
                                  "entry": str(x.entry), "exit": str(x.exit),
                                  "gross_return": str(x.gross_return), "net_return": str(x.net_return),
                                  "volume_ratio": str(x.volume_ratio)} for x in ai_selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report["deterministic"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from research.historical_experiment import Candidate, generate_candidates, load_symbol


@dataclass(frozen=True)
class ConditionResult:
    volume_ratio_minimum: str
    maximum_daily_entries: int
    sessions: int
    active_days: int
    trades: int
    no_trade_rate: float
    direction_accuracy: float | None
    cumulative_net_return: float
    mean_net_return: float | None
    maximum_drawdown: float
    worst_trade: float | None


def select_daily(
    candidates: Iterable[Candidate], *, threshold: Decimal, maximum_daily_entries: int
) -> tuple[Candidate, ...]:
    if maximum_daily_entries not in {1, 2}:
        raise ValueError("MAXIMUM_DAILY_ENTRIES_UNSUPPORTED")
    selected: list[Candidate] = []
    by_day: dict[date, list[Candidate]] = {}
    for item in sorted(candidates, key=lambda row: (row.timestamp, row.symbol)):
        if item.volume_ratio >= threshold:
            by_day.setdefault(item.timestamp.date(), []).append(item)
    for day in sorted(by_day):
        used_symbols: set[str] = set()
        for item in by_day[day]:
            if item.symbol in used_symbols:
                continue
            selected.append(item)
            used_symbols.add(item.symbol)
            if len(used_symbols) >= maximum_daily_entries:
                break
    return tuple(selected)


def summarize_condition(
    selected: tuple[Candidate, ...], *, threshold: Decimal,
    maximum_daily_entries: int, sessions: int,
) -> ConditionResult:
    pnl = [float(item.net_return) for item in selected]
    active_days = len({item.timestamp.date() for item in selected})
    equity = peak = drawdown = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return ConditionResult(
        str(threshold), maximum_daily_entries, sessions, active_days, len(selected),
        1 - active_days / sessions if sessions else 1.0,
        sum(item.gross_return > 0 for item in selected) / len(selected) if selected else None,
        sum(pnl), sum(pnl) / len(pnl) if pnl else None, drawdown,
        min(pnl) if pnl else None,
    )


def run_comparison(data_dir: Path, symbols: tuple[str, ...]) -> dict[str, object]:
    data = {path.stem.upper(): load_symbol(path) for path in data_dir.glob("*.json")}
    candidates = generate_candidates(data, symbols=symbols)
    sessions = len({bar.started_at.date() for bar in data["SPY"]})
    thresholds = (Decimal("1.5"), Decimal("2.0"), Decimal("2.5"), Decimal("3.0"))
    results = []
    for threshold in thresholds:
        for cap in (1, 2):
            selected = select_daily(candidates, threshold=threshold, maximum_daily_entries=cap)
            results.append(summarize_condition(
                selected, threshold=threshold, maximum_daily_entries=cap, sessions=sessions
            ))
    return {
        "schema_version": 1,
        "method": "UNDERLYING_DIRECTION_PROXY_NOT_OPTION_PNL",
        "sessions": sessions,
        "candidate_events": len(candidates),
        "round_trip_friction_bps": 10,
        "holding_minutes": 30,
        "daily_cap_2_requires_distinct_symbols": True,
        "results": [asdict(item) for item in results],
        "limitations": [
            "No historical option bid/ask, IV, Greeks, or exit quote is available.",
            "The same data is used for comparison; results are exploratory, not a promotion decision.",
            "Multiple-testing and small-sample risk remain.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    args = parser.parse_args()
    report = run_comparison(args.data_dir, tuple(args.symbols))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

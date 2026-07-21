from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from journal.shadow_review import review_shadow_day


@dataclass(frozen=True)
class ShadowExperimentReport:
    start_date: date
    end_date: date
    observed_days: int
    eligible_days: int
    completed_trades: int
    pilot_decisions: int
    total_errors: int
    hard_rule_violations: int
    net_pnl_usd: Decimal
    gross_profit_usd: Decimal
    gross_loss_usd: Decimal
    profit_factor: Decimal | None
    maximum_drawdown_usd: Decimal
    maximum_drawdown_pct_of_initial_capital: Decimal
    gates: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "observed_days": self.observed_days,
            "eligible_days": self.eligible_days,
            "completed_trades": self.completed_trades,
            "pilot_decisions": self.pilot_decisions,
            "total_errors": self.total_errors,
            "hard_rule_violations": self.hard_rule_violations,
            "net_pnl_usd": str(self.net_pnl_usd),
            "gross_profit_usd": str(self.gross_profit_usd),
            "gross_loss_usd": str(self.gross_loss_usd),
            "profit_factor": str(self.profit_factor) if self.profit_factor is not None else None,
            "maximum_drawdown_usd": str(self.maximum_drawdown_usd),
            "maximum_drawdown_pct_of_initial_capital": str(
                self.maximum_drawdown_pct_of_initial_capital
            ),
            "gates": self.gates,
            "all_validation_gates_passed": all(self.gates.values()),
        }


def build_shadow_experiment_report(
    *,
    root: str | Path = "logs/shadow",
    initial_capital_usd: Decimal = Decimal("300"),
    minimum_days: int = 40,
    minimum_trades: int = 30,
    minimum_profit_factor: Decimal = Decimal("1.20"),
    maximum_drawdown_pct: Decimal = Decimal("15"),
) -> ShadowExperimentReport:
    root_path = Path(root)
    dated_dirs: list[tuple[date, Path]] = []
    if root_path.exists():
        for child in root_path.iterdir():
            if child.is_dir():
                try:
                    dated_dirs.append((date.fromisoformat(child.name), child))
                except ValueError:
                    continue
    if not dated_dirs:
        raise ValueError("No dated Shadow logs were found.")
    dated_dirs.sort()

    eligible_days = 0
    trades = 0
    pilot_decisions = 0
    errors = 0
    violations = 0
    timestamped_pnl: list[tuple[str, Decimal]] = []
    for day, directory in dated_dirs:
        daily = review_shadow_day(day, root=root_path)
        eligible_days += int(daily.to_dict()["strategy_evidence_eligible"])
        pilot_decisions += daily.pilot_decisions
        errors += daily.error_sessions
        violations += daily.hard_rule_violations
        for log_path in sorted(directory.glob("*.jsonl")):
            session_pnl: list[tuple[str, Decimal]] = []
            session_is_pilot = False
            for line in log_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event_type") == "DECISION_RECORDED":
                    session_is_pilot = "PILOT_NOT_STRATEGY_EVIDENCE" in (
                        event.get("payload", {}).get("reasons") or []
                    )
                if event.get("event_type") == "EXITED":
                    recorded_at = event.get("recorded_at")
                    if not isinstance(recorded_at, str):
                        raise ValueError(f"EXITED event missing recorded_at: {log_path}")
                    session_pnl.append((recorded_at, Decimal(str(event["payload"]["simulated_pnl_usd"]))))
            if not session_is_pilot:
                timestamped_pnl.extend(session_pnl)
                trades += len(session_pnl)

    timestamped_pnl.sort(key=lambda item: item[0])
    pnl_series = [value for _, value in timestamped_pnl]

    gross_profit = sum((value for value in pnl_series if value > 0), Decimal("0"))
    gross_loss = -sum((value for value in pnl_series if value < 0), Decimal("0"))
    net_pnl = sum(pnl_series, Decimal("0"))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = initial_capital_usd
    peak = initial_capital_usd
    maximum_drawdown = Decimal("0")
    for value in pnl_series:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    drawdown_pct = maximum_drawdown / initial_capital_usd * Decimal("100")

    gates = {
        "minimum_shadow_days": eligible_days >= minimum_days,
        "minimum_completed_trades": trades >= minimum_trades,
        "positive_net_pnl": net_pnl > 0,
        "minimum_profit_factor": profit_factor is not None and profit_factor >= minimum_profit_factor,
        "maximum_drawdown": drawdown_pct <= maximum_drawdown_pct,
        "zero_system_error_sessions": errors == 0,
        "zero_hard_rule_violations": violations == 0,
        "zero_pilot_decisions": pilot_decisions == 0,
    }
    return ShadowExperimentReport(
        dated_dirs[0][0], dated_dirs[-1][0], len(dated_dirs), eligible_days,
        trades, pilot_decisions, errors, violations, net_pnl, gross_profit,
        gross_loss, profit_factor, maximum_drawdown, drawdown_pct, gates,
    )

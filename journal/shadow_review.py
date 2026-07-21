from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class InvalidShadowLogError(ValueError):
    pass


@dataclass(frozen=True)
class ShadowDailyReview:
    session_date: date
    session_count: int
    completed_sessions: int
    error_sessions: int
    decision_count: int
    no_trade_decisions: int
    rejected_decisions: int
    simulated_entry_candidates: int
    pilot_decisions: int
    simulated_fills: int
    unfilled_entries: int
    simulated_exits: int
    simulated_net_pnl_usd: Decimal
    reason_counts: dict[str, int]
    system_error_counts: dict[str, int]
    hard_rule_violations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date.isoformat(),
            "session_count": self.session_count,
            "completed_sessions": self.completed_sessions,
            "error_sessions": self.error_sessions,
            "decision_count": self.decision_count,
            "no_trade_decisions": self.no_trade_decisions,
            "rejected_decisions": self.rejected_decisions,
            "simulated_entry_candidates": self.simulated_entry_candidates,
            "pilot_decisions": self.pilot_decisions,
            "simulated_fills": self.simulated_fills,
            "unfilled_entries": self.unfilled_entries,
            "simulated_exits": self.simulated_exits,
            "simulated_net_pnl_usd": str(self.simulated_net_pnl_usd),
            "reason_counts": self.reason_counts,
            "system_error_counts": self.system_error_counts,
            "hard_rule_violations": self.hard_rule_violations,
            "strategy_evidence_eligible": self._evidence_eligible(),
        }

    def _evidence_eligible(self) -> bool:
        return (
            self.error_sessions == 0
            and self.hard_rule_violations == 0
            and self.pilot_decisions == 0
            and self.session_count > 0
        )


def _decimal(value: Any, path: Path) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidShadowLogError(f"Invalid decimal in {path}: {value!r}") from error


def review_shadow_day(
    session_date: date,
    *,
    root: str | Path = "logs/shadow",
) -> ShadowDailyReview:
    day = Path(root) / session_date.isoformat()
    files = sorted(day.glob("*.jsonl")) if day.exists() else []
    reasons: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    completed_sessions = 0
    error_sessions = 0
    decision_count = 0
    no_trade = 0
    rejected = 0
    candidates = 0
    pilot_decisions = 0
    fills = 0
    unfilled = 0
    exits = 0
    pnl = Decimal("0")
    hard_violations = 0

    for path in files:
        expected_sequence = 1
        terminal: str | None = None
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise InvalidShadowLogError(f"Invalid JSON at {path}:{line_number}") from error
            if event.get("sequence") != expected_sequence:
                raise InvalidShadowLogError(f"Broken sequence at {path}:{line_number}")
            expected_sequence += 1
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise InvalidShadowLogError(f"Missing payload at {path}:{line_number}")

            if event_type == "DECISION_RECORDED":
                decision_count += 1
                status = payload.get("status")
                if status == "NO_TRADE":
                    no_trade += 1
                elif status == "REJECTED":
                    rejected += 1
                elif status == "PILOT_SIMULATED_ENTRY":
                    candidates += 1
                for reason in payload.get("reasons") or []:
                    reasons[str(reason)] += 1
                    if str(reason) == "PILOT_NOT_STRATEGY_EVIDENCE":
                        pilot_decisions += 1
                if payload.get("hard_rule_rejection") is True:
                    hard_violations += 1
            elif event_type == "ENTRY_FILLED":
                fills += 1
            elif event_type == "ENTRY_UNFILLED":
                unfilled += 1
                terminal = "COMPLETE"
            elif event_type == "EXITED":
                exits += 1
                pnl += _decimal(payload.get("simulated_pnl_usd"), path)
                terminal = "COMPLETE"
            elif event_type == "SESSION_COMPLETED":
                terminal = "COMPLETE"
            elif event_type == "SESSION_ERROR":
                error_sessions += 1
                errors[str(payload.get("reason") or "UNKNOWN")] += 1
                terminal = "ERROR"
        if terminal == "COMPLETE":
            completed_sessions += 1
        elif terminal is None:
            errors["NON_TERMINAL_SESSION_LOG"] += 1
            error_sessions += 1

    return ShadowDailyReview(
        session_date, len(files), completed_sessions, error_sessions, decision_count,
        no_trade, rejected, candidates, pilot_decisions, fills, unfilled, exits, pnl,
        dict(sorted(reasons.items())), dict(sorted(errors.items())), hard_violations,
    )

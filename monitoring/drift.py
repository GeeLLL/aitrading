from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class DriftReport:
    drifted: bool
    mean_shift: Decimal | None
    no_trade_rate: Decimal | None
    schema_failure_rate: Decimal | None
    reasons: tuple[str, ...]


def evaluate_drift(
    *, reference_values: Iterable[Decimal], current_values: Iterable[Decimal],
    no_trade_flags: Iterable[bool], schema_failures: Iterable[bool],
    maximum_relative_mean_shift: Decimal = Decimal("0.25"),
    maximum_no_trade_rate: Decimal = Decimal("0.95"),
    maximum_schema_failure_rate: Decimal = Decimal("0.01"),
) -> DriftReport:
    reference, current = tuple(reference_values), tuple(current_values)
    no_trades, failures = tuple(no_trade_flags), tuple(schema_failures)
    reasons: list[str] = []
    shift = None
    if not reference or not current:
        reasons.append("FEATURE_SAMPLE_MISSING")
    else:
        ref_mean = sum(reference, Decimal("0")) / Decimal(len(reference))
        cur_mean = sum(current, Decimal("0")) / Decimal(len(current))
        shift = abs(cur_mean - ref_mean) / abs(ref_mean) if ref_mean else (Decimal("0") if cur_mean == 0 else None)
        if shift is None or shift > maximum_relative_mean_shift:
            reasons.append("FEATURE_MEAN_DRIFT")
    no_trade_rate = sum(no_trades) / Decimal(len(no_trades)) if no_trades else None
    failure_rate = sum(failures) / Decimal(len(failures)) if failures else None
    if no_trade_rate is None or no_trade_rate > maximum_no_trade_rate:
        reasons.append("NO_TRADE_RATE_ALERT")
    if failure_rate is None or failure_rate > maximum_schema_failure_rate:
        reasons.append("SCHEMA_FAILURE_RATE_ALERT")
    return DriftReport(bool(reasons), shift, no_trade_rate, failure_rate, tuple(reasons))

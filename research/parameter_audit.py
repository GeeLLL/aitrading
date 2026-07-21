from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


MANDATORY_PARAMETERS = frozenset({
    "entry_delay_minutes", "stop_new_entries_before_close_minutes", "dte_min", "dte_max",
    "delta_min", "delta_max", "minimum_volume", "minimum_open_interest", "maximum_relative_spread",
    "maximum_quote_age_seconds", "stage_1_premium_cap", "absolute_premium_cap", "stop_loss_pct",
    "profit_target_pct", "maximum_holding_minutes", "earnings_blackout_days", "sampling_interval_minutes",
})


@dataclass(frozen=True)
class ParameterAudit:
    complete: bool
    missing: tuple[str, ...]
    unversioned: tuple[str, ...]


def audit_parameters(values: Mapping[str, object], evidence_versions: Mapping[str, str]) -> ParameterAudit:
    missing = tuple(sorted(MANDATORY_PARAMETERS - values.keys()))
    unversioned = tuple(sorted(name for name in MANDATORY_PARAMETERS if name in values and not evidence_versions.get(name)))
    return ParameterAudit(not missing and not unversioned, missing, unversioned)

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EvidenceEligibility:
    eligible: bool
    population: str
    reasons: tuple[str, ...]


def classify_shadow_evidence(
    record: Mapping[str, object],
    *,
    authorization_verified: bool = False,
) -> EvidenceEligibility:
    """Classify one run before it can enter strategy-performance statistics.

    authorization_verified must come from independently loading the persisted
    state/shadow_authorization.json record. A run's own governance strings are
    written by the collection agent and are never sufficient on their own.
    """
    reasons: list[str] = []
    governance = record.get("governance") if isinstance(record.get("governance"), dict) else {}
    safety = record.get("safety") if isinstance(record.get("safety"), dict) else {}
    violations = record.get("rule_violations") if isinstance(record.get("rule_violations"), dict) else {}
    if governance.get("formal_shadow_authorized") is not True:
        reasons.append("FORMAL_SHADOW_NOT_AUTHORIZED")
    elif not authorization_verified:
        reasons.append("AUTHORIZATION_RECORD_NOT_VERIFIED")
    if governance.get("performance_eligibility") != "FORMAL_SHADOW_ELIGIBLE":
        reasons.append("PERFORMANCE_POPULATION_NOT_FORMAL")
    if safety.get("system_mode") != "READ_ONLY":
        reasons.append("SYSTEM_NOT_READ_ONLY")
    if safety.get("live_trading_enabled") is not False or safety.get("order_tools_enabled") is not False:
        reasons.append("MUTATION_BOUNDARY_NOT_DISABLED")
    if record.get("status") != "COMPLETED":
        reasons.append("RUN_NOT_COMPLETED")
    if record.get("missing"):
        reasons.append("REQUIRED_DATA_MISSING")
    if record.get("stale"):
        reasons.append("STALE_DATA_PRESENT")
    if record.get("anomalies"):
        reasons.append("DATA_ANOMALY_PRESENT")
    if any(int(value or 0) for value in violations.values()):
        reasons.append("RULE_VIOLATION_PRESENT")
    event = str(record.get("event") or "")
    if "PILOT" in event:
        population = "PILOT"
    elif "DRILL" in event:
        population = "DRILL"
    else:
        population = "FORMAL_SHADOW" if not reasons else "REJECTED_OR_INELIGIBLE"
    return EvidenceEligibility(not reasons, population, tuple(dict.fromkeys(reasons)))

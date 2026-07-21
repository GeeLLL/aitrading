from __future__ import annotations

from dataclasses import dataclass


FAULTS = (
    "OAUTH_EXPIRED", "MCP_DISCONNECTED", "QUOTE_MISSING", "QUOTE_STALE",
    "ORDER_STATE_UNKNOWN", "DUPLICATE_INTENT", "PROCESS_CRASH",
    "CASH_MISMATCH", "POSITION_MISMATCH", "KILL_SWITCH_CORRUPT",
    "EARLY_CLOSE", "CLOCK_SKEW", "PARTIAL_FILL", "CANCEL_FILL_RACE",
)


@dataclass(frozen=True)
class FaultOutcome:
    fault: str
    expected_action: str
    new_entries_blocked: bool


def expected_fault_matrix() -> tuple[FaultOutcome, ...]:
    exit_only = {"PARTIAL_FILL", "CANCEL_FILL_RACE"}
    return tuple(
        FaultOutcome(fault, "RECONCILE_AND_EXIT_ONLY" if fault in exit_only else "HALT_AND_ALERT", True)
        for fault in FAULTS
    )


def qualify_fault_matrix(outcomes: tuple[FaultOutcome, ...]) -> tuple[bool, tuple[str, ...]]:
    observed = {item.fault: item for item in outcomes}
    reasons = [f"FAULT_NOT_TESTED:{fault}" for fault in FAULTS if fault not in observed]
    reasons.extend(f"FAULT_DID_NOT_BLOCK:{fault}" for fault, item in observed.items() if not item.new_entries_blocked)
    return not reasons, tuple(reasons)

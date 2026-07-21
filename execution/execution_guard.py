from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from execution.order_state import DurableOrder, ReconciliationDecision, reconcile_order_state
from monitoring.kill_switch import KillSwitch, KillSwitchStatus


@dataclass(frozen=True)
class ExecutionGuardDecision:
    allowed: bool
    reasons: tuple[str, ...]
    kill_switch: KillSwitchStatus
    reconciliation: ReconciliationDecision


def evaluate_execution_boundary(
    *,
    local_orders: Iterable[DurableOrder],
    broker_open_idempotency_keys: set[str] | None,
    broker_position_count: int | None,
    kill_switch: KillSwitch | None = None,
) -> ExecutionGuardDecision:
    """Independent final gate; this module has no broker mutation methods."""

    status = (kill_switch or KillSwitch()).status()
    reconciliation = reconcile_order_state(
        local_orders,
        broker_open_idempotency_keys=broker_open_idempotency_keys,
        broker_position_count=broker_position_count,
    )
    reasons: list[str] = []
    if status.engaged:
        reasons.append(f"KILL_SWITCH_ENGAGED:{status.reason}")
    if not reconciliation.safe:
        reasons.extend(reconciliation.reasons)
    return ExecutionGuardDecision(not reasons, tuple(reasons), status, reconciliation)

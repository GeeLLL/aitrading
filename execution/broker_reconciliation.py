from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from execution.order_state import DurableOrder, OrderState, TERMINAL_STATES


class ReconciliationMode(str, Enum):
    BLOCKED = "BLOCKED"
    READ_ONLY_SAFE = "READ_ONLY_SAFE"


@dataclass(frozen=True)
class BrokerOrderView:
    idempotency_key: str | None
    state: str | None
    quantity: int | None


@dataclass(frozen=True)
class BrokerPositionView:
    instrument_id: str | None
    quantity: int | None
    option_type: str | None


@dataclass(frozen=True)
class StartupReconciliation:
    mode: ReconciliationMode
    reasons: tuple[str, ...]


def reconcile_on_startup(
    *,
    local_orders: Iterable[DurableOrder],
    broker_orders: Iterable[BrokerOrderView] | None,
    broker_positions: Iterable[BrokerPositionView] | None,
) -> StartupReconciliation:
    if broker_orders is None or broker_positions is None:
        return StartupReconciliation(ReconciliationMode.BLOCKED, ("BROKER_STATE_UNKNOWN",))
    orders, positions = tuple(broker_orders), tuple(broker_positions)
    reasons: list[str] = []
    if any(order.idempotency_key is None or order.state is None or order.quantity is None for order in orders):
        reasons.append("BROKER_ORDER_IDENTITY_UNKNOWN")
    if any(position.instrument_id is None or position.quantity is None or position.option_type not in {"CALL", "PUT"} for position in positions):
        reasons.append("BROKER_POSITION_IDENTITY_UNKNOWN")
    if any(order.quantity != 1 for order in orders if order.quantity is not None):
        reasons.append("BROKER_ORDER_QUANTITY_INVALID")
    if any(position.quantity != 1 for position in positions if position.quantity is not None):
        reasons.append("BROKER_POSITION_QUANTITY_INVALID")
    if len(positions) > 1:
        reasons.append("MORE_THAN_ONE_BROKER_POSITION")
    local_active = {
        order.idempotency_key for order in local_orders
        if order.state not in TERMINAL_STATES and order.state is not OrderState.FILLED
    }
    broker_active = {order.idempotency_key for order in orders if order.idempotency_key is not None}
    if local_active != broker_active:
        reasons.append("OPEN_ORDER_RECONCILIATION_MISMATCH")
    local_positions = sum(order.state is OrderState.FILLED for order in local_orders)
    if local_positions != len(positions):
        reasons.append("POSITION_RECONCILIATION_MISMATCH")
    return StartupReconciliation(
        ReconciliationMode.READ_ONLY_SAFE if not reasons else ReconciliationMode.BLOCKED,
        tuple(dict.fromkeys(reasons)),
    )

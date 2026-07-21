from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class OrderState(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"
    HALTED_UNKNOWN_STATE = "HALTED_UNKNOWN_STATE"


TERMINAL_STATES = {OrderState.CANCELLED, OrderState.REJECTED, OrderState.CLOSED}

ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.INTENT_CREATED: {OrderState.VALIDATED, OrderState.REJECTED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.VALIDATED: {OrderState.SUBMITTING, OrderState.REJECTED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.SUBMITTING: {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.ACKNOWLEDGED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.REJECTED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_PENDING, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.FILLED: {OrderState.CLOSED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.CANCEL_PENDING: {OrderState.CANCELLED, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.HALTED_UNKNOWN_STATE},
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.CLOSED: set(),
    OrderState.HALTED_UNKNOWN_STATE: set(),
}


@dataclass(frozen=True)
class DurableOrder:
    intent_id: str
    idempotency_key: str
    state: OrderState
    requested_quantity: int
    filled_quantity: int
    updated_at: str


class DurableOrderStore:
    """Atomic local order state with no Robinhood submission capability."""

    def __init__(self, root: str | Path = "state/orders") -> None:
        self.root = Path(root)

    def _path(self, intent_id: str) -> Path:
        if not intent_id or not all(char.isalnum() or char in "-_" for char in intent_id):
            raise ValueError("intent_id contains unsafe characters")
        return self.root / f"{intent_id}.json"

    def load(self, intent_id: str) -> DurableOrder | None:
        path = self._path(intent_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DurableOrder(
            intent_id=str(raw["intent_id"]),
            idempotency_key=str(raw["idempotency_key"]),
            state=OrderState(raw["state"]),
            requested_quantity=int(raw["requested_quantity"]),
            filled_quantity=int(raw["filled_quantity"]),
            updated_at=str(raw["updated_at"]),
        )

    def create(self, *, intent_id: str, idempotency_key: str, quantity: int) -> DurableOrder:
        if quantity != 1 or not idempotency_key:
            raise ValueError("Only one-contract orders with an idempotency key are allowed.")
        if self.load(intent_id) is not None:
            raise ValueError("Duplicate intent_id")
        for existing in self.list_orders():
            if existing.idempotency_key == idempotency_key:
                raise ValueError("Duplicate idempotency_key")
        order = DurableOrder(intent_id, idempotency_key, OrderState.INTENT_CREATED, quantity, 0, _now())
        self._write(order)
        return order

    def transition(self, intent_id: str, new_state: OrderState, *, filled_quantity: int | None = None) -> DurableOrder:
        current = self.load(intent_id)
        if current is None:
            raise ValueError("Unknown intent_id")
        if new_state not in ALLOWED_TRANSITIONS[current.state]:
            raise ValueError(f"Invalid transition {current.state.value} -> {new_state.value}")
        fill = current.filled_quantity if filled_quantity is None else filled_quantity
        if not 0 <= fill <= current.requested_quantity:
            raise ValueError("Invalid filled quantity")
        if new_state is OrderState.FILLED and fill != current.requested_quantity:
            raise ValueError("FILLED requires the full quantity")
        updated = DurableOrder(
            current.intent_id, current.idempotency_key, new_state,
            current.requested_quantity, fill, _now(),
        )
        self._write(updated)
        return updated

    def list_orders(self) -> tuple[DurableOrder, ...]:
        if not self.root.exists():
            return ()
        orders = [self.load(path.stem) for path in sorted(self.root.glob("*.json"))]
        return tuple(order for order in orders if order is not None)

    def _write(self, order: DurableOrder) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(order.intent_id)
        temp = path.with_suffix(".tmp")
        payload = asdict(order)
        payload["state"] = order.state.value
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)


@dataclass(frozen=True)
class ReconciliationDecision:
    safe: bool
    reasons: tuple[str, ...]


def reconcile_order_state(
    local_orders: Iterable[DurableOrder],
    *,
    broker_open_idempotency_keys: set[str] | None,
    broker_position_count: int | None,
) -> ReconciliationDecision:
    """Fail closed when local and broker-visible state cannot be reconciled."""

    if broker_open_idempotency_keys is None or broker_position_count is None:
        return ReconciliationDecision(False, ("BROKER_STATE_UNKNOWN",))
    reasons: list[str] = []
    local_active = {
        order.idempotency_key for order in local_orders
        if order.state not in TERMINAL_STATES and order.state is not OrderState.FILLED
    }
    if local_active != broker_open_idempotency_keys:
        reasons.append("OPEN_ORDER_RECONCILIATION_MISMATCH")
    local_positions = sum(order.state is OrderState.FILLED for order in local_orders)
    if local_positions != broker_position_count:
        reasons.append("POSITION_RECONCILIATION_MISMATCH")
    if broker_position_count > 1:
        reasons.append("MORE_THAN_ONE_BROKER_POSITION")
    return ReconciliationDecision(not reasons, tuple(reasons))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

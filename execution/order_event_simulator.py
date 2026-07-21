from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrokerEvent(str, Enum):
    SUBMIT_ACK = "SUBMIT_ACK"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACK = "CANCEL_ACK"
    REJECT = "REJECT"
    DISCONNECT = "DISCONNECT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SimulatedOrderState:
    status: str
    filled_quantity: int
    new_entries_blocked: bool
    reasons: tuple[str, ...]


def simulate_order_events(events: tuple[BrokerEvent, ...], *, quantity: int = 1) -> SimulatedOrderState:
    """Exercise broker races without any broker or order tool dependency."""
    if quantity != 1:
        raise ValueError("ONLY_ONE_CONTRACT_SUPPORTED")
    status = "SUBMITTING"
    filled = 0
    blocked = False
    reasons: list[str] = []
    cancel_requested = False
    for event in events:
        if event in {BrokerEvent.DISCONNECT, BrokerEvent.UNKNOWN}:
            blocked = True
            status = "HALTED_UNKNOWN_STATE"
            reasons.append("BROKER_STATE_UNKNOWN")
            break
        if event is BrokerEvent.REJECT:
            if filled:
                blocked = True
                status = "HALTED_UNKNOWN_STATE"
                reasons.append("REJECT_AFTER_FILL_CONTRADICTION")
            else:
                status = "REJECTED"
            continue
        if event is BrokerEvent.SUBMIT_ACK:
            if status != "SUBMITTING":
                blocked = True
                status = "HALTED_UNKNOWN_STATE"
                reasons.append("UNEXPECTED_SUBMIT_ACK")
                break
            status = "ACKNOWLEDGED"
            continue
        if event is BrokerEvent.CANCEL_REQUESTED:
            if status not in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
                blocked = True
                status = "HALTED_UNKNOWN_STATE"
                reasons.append("INVALID_CANCEL_STATE")
                break
            cancel_requested = True
            status = "CANCEL_PENDING"
            continue
        if event in {BrokerEvent.PARTIAL_FILL, BrokerEvent.FULL_FILL}:
            if status not in {"ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING"}:
                blocked = True
                status = "HALTED_UNKNOWN_STATE"
                reasons.append("FILL_WITHOUT_ACK")
                break
            # A fill racing with cancel is valid and must win over cancellation.
            filled = quantity if event is BrokerEvent.FULL_FILL else max(filled, 0)
            status = "FILLED" if filled == quantity else "PARTIALLY_FILLED"
            if cancel_requested and filled:
                reasons.append("FILL_WON_CANCEL_RACE")
            continue
        if event is BrokerEvent.CANCEL_ACK:
            if not cancel_requested:
                blocked = True
                status = "HALTED_UNKNOWN_STATE"
                reasons.append("CANCEL_ACK_WITHOUT_REQUEST")
                break
            if filled == quantity:
                reasons.append("LATE_CANCEL_ACK_IGNORED_AFTER_FILL")
                status = "FILLED"
            else:
                status = "CANCELLED"
            continue
    return SimulatedOrderState(status, filled, blocked, tuple(dict.fromkeys(reasons)))

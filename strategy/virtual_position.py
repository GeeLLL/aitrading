from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class VirtualPositionState(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class VirtualPosition:
    trade_id: str
    state: VirtualPositionState
    quantity: int
    limit_price: Decimal
    fill_price: Decimal | None = None
    opened_at: datetime | None = None
    exit_price: Decimal | None = None
    closed_at: datetime | None = None
    exit_reason: str | None = None
    gross_pnl_usd: Decimal | None = None
    friction_usd: Decimal = Decimal("0")
    net_pnl_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity != 1:
            raise ValueError("VIRTUAL_POSITION_QUANTITY_MUST_EQUAL_ONE")
        if self.limit_price <= 0 or self.friction_usd < 0:
            raise ValueError("VIRTUAL_POSITION_PRICE_INVALID")


def open_virtual_position(
    position: VirtualPosition, *, observed_ask: Decimal | None, observed_at: datetime,
    entry_fees_usd: Decimal = Decimal("0"),
) -> VirtualPosition:
    if position.state is not VirtualPositionState.PENDING:
        raise ValueError("VIRTUAL_POSITION_NOT_PENDING")
    if observed_ask is None or observed_ask <= 0:
        return _replace(position, state=VirtualPositionState.REJECTED, exit_reason="ENTRY_QUOTE_UNKNOWN")
    if observed_ask > position.limit_price:
        return position
    return _replace(
        position, state=VirtualPositionState.OPEN, fill_price=observed_ask,
        opened_at=observed_at, friction_usd=entry_fees_usd,
    )


def close_virtual_position(
    position: VirtualPosition, *, current_bid: Decimal | None, observed_at: datetime,
    reason: str, exit_fees_usd: Decimal = Decimal("0"),
) -> VirtualPosition:
    if position.state is not VirtualPositionState.OPEN or position.fill_price is None:
        raise ValueError("VIRTUAL_POSITION_NOT_OPEN")
    if current_bid is None or current_bid < 0:
        return _replace(position, state=VirtualPositionState.MANUAL_REVIEW, exit_reason="EXIT_QUOTE_UNKNOWN")
    gross = (current_bid - position.fill_price) * Decimal("100") * Decimal(position.quantity)
    friction = position.friction_usd + exit_fees_usd
    return _replace(
        position, state=VirtualPositionState.CLOSED, exit_price=current_bid,
        closed_at=observed_at, exit_reason=reason, gross_pnl_usd=gross,
        friction_usd=friction, net_pnl_usd=gross - friction,
    )


def _replace(position: VirtualPosition, **changes: object) -> VirtualPosition:
    values = position.__dict__ | changes
    return VirtualPosition(**values)

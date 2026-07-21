from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class OrderAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionEffect(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


@dataclass(frozen=True)
class OrderIntent:
    underlying: str
    option_type: OptionType
    action: OrderAction
    position_effect: PositionEffect
    order_type: OrderType
    quantity: int
    limit_price: Decimal
    expiration: date

    bid: Decimal | None
    ask: Decimal | None
    quote_updated_at: datetime | None
    volume: int | None
    open_interest: int | None


@dataclass(frozen=True)
class AccountSnapshot:
    account_type: str | None
    equity: Decimal | None
    buying_power: Decimal | None

    option_position_count: int | None
    equity_position_count: int | None
    open_option_order_count: int | None
    open_equity_order_count: int | None

    consecutive_losses: int | None
    entries_today: int | None

    # Cash-account fields are deliberately separate. Buying power is not proof
    # that proceeds are settled or unreserved.
    settled_cash: Decimal | None = None
    unsettled_cash: Decimal | None = None
    reserved_cash: Decimal | None = None


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    violations: tuple[str, ...]
    estimated_premium_usd: Decimal | None

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING


@dataclass(frozen=True)
class ExecutionAssumptions:
    contracts: int
    entry_bid: Decimal
    entry_ask: Decimal
    exit_bid: Decimal
    exit_ask: Decimal
    entry_ask_size: int | None
    exit_bid_size: int | None
    tick_size: Decimal
    latency_slippage_ticks: int
    per_contract_fees: Decimal
    regulatory_exit_fee: Decimal


@dataclass(frozen=True)
class ExecutionEstimate:
    filled_contracts: int
    entry_price: Decimal | None
    exit_price: Decimal | None
    net_pnl_usd: Decimal | None
    total_friction_usd: Decimal | None
    reasons: tuple[str, ...]


def estimate_execution(value: ExecutionAssumptions) -> ExecutionEstimate:
    if value.contracts <= 0 or value.tick_size <= 0 or value.latency_slippage_ticks < 0:
        raise ValueError("EXECUTION_ASSUMPTION_INVALID")
    if value.entry_ask < value.entry_bid or value.exit_ask < value.exit_bid:
        raise ValueError("QUOTE_CROSSED")
    if value.entry_ask_size is None or value.exit_bid_size is None:
        return ExecutionEstimate(0, None, None, None, None, ("DISPLAYED_SIZE_UNKNOWN",))
    filled = min(value.contracts, value.entry_ask_size, value.exit_bid_size)
    if filled <= 0:
        return ExecutionEstimate(0, None, None, None, None, ("NO_DISPLAYED_LIQUIDITY",))
    adverse = value.tick_size * value.latency_slippage_ticks
    entry = value.entry_ask + adverse
    exit_price = max(Decimal("0"), value.exit_bid - adverse)
    fees = Decimal(filled) * value.per_contract_fees * Decimal("2") + value.regulatory_exit_fee
    net = (exit_price - entry) * Decimal("100") * Decimal(filled) - fees
    mid_entry = (value.entry_bid + value.entry_ask) / Decimal("2")
    mid_exit = (value.exit_bid + value.exit_ask) / Decimal("2")
    friction = ((entry - mid_entry) + (mid_exit - exit_price)) * Decimal("100") * Decimal(filled) + fees
    reasons = () if filled == value.contracts else ("PARTIAL_FILL",)
    return ExecutionEstimate(filled, entry, exit_price, net, friction, reasons)

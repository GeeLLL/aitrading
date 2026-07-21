from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from execution.broker_reconciliation import (
    BrokerOrderView,
    BrokerPositionView,
    ReconciliationMode,
    reconcile_on_startup,
)
from execution.order_state import DurableOrder
from risk.cash_ledger import CashLedger
from risk.cash_reconciliation import OfficialCashView, reconcile_cash_ledger


@dataclass(frozen=True)
class StartupGateReport:
    safe_for_observation: bool
    safe_for_new_entry: bool
    reasons: tuple[str, ...]


def evaluate_startup_gate(
    *,
    local_orders: Iterable[DurableOrder],
    broker_orders: Iterable[BrokerOrderView] | None,
    broker_positions: Iterable[BrokerPositionView] | None,
    local_cash: CashLedger,
    official_cash: OfficialCashView,
    system_read_only: bool,
    live_trading_enabled: bool,
    order_tools_enabled: bool,
    kill_switch_engaged: bool,
) -> StartupGateReport:
    """Combine broker, cash, and safety state into one fail-closed restart gate.

    Observation can remain available while the account is blocked from new
    entries. This function never calls a broker and cannot submit an order.
    """
    broker = reconcile_on_startup(
        local_orders=local_orders,
        broker_orders=broker_orders,
        broker_positions=broker_positions,
    )
    cash = reconcile_cash_ledger(local_cash, official_cash)
    reasons = list(broker.reasons) + list(cash.reasons)
    if system_read_only:
        reasons.append("SYSTEM_READ_ONLY")
    if not live_trading_enabled:
        reasons.append("LIVE_TRADING_DISABLED")
    if not order_tools_enabled:
        reasons.append("ORDER_TOOLS_DISABLED")
    if kill_switch_engaged:
        reasons.append("KILL_SWITCH_ENGAGED")

    reconciliation_safe = broker.mode is ReconciliationMode.READ_ONLY_SAFE and cash.safe
    safety_allows_entry = (
        not system_read_only
        and live_trading_enabled
        and order_tools_enabled
        and not kill_switch_engaged
    )
    return StartupGateReport(
        safe_for_observation=reconciliation_safe,
        safe_for_new_entry=reconciliation_safe and safety_allows_entry,
        reasons=tuple(dict.fromkeys(reasons)),
    )

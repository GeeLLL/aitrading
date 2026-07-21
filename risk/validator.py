from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from risk.models import (
    AccountSnapshot,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionEffect,
    RiskDecision,
)
from risk.startup_guard import UnsafeConfigurationError, validate_safety_config


ONE_HUNDRED = Decimal("100")
ZERO = Decimal("0")


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def evaluate_opening_order(
    *,
    intent: OrderIntent,
    account: AccountSnapshot,
    config: dict[str, Any],
    now: datetime,
    completed_live_trades: int | None,
    market_open: bool | None,
    within_entry_window: bool | None,
    near_forced_exit: bool | None,
    max_quote_age_seconds: int | None,
    max_relative_spread: Decimal | None,
    minimum_option_volume: int | None,
    minimum_open_interest: int | None,
    kill_switch_engaged: bool | None = None,
    kill_switch_reason: str | None = None,
) -> RiskDecision:
    """Evaluate a proposed opening order without sending it anywhere.

    Every unknown required value is treated as a violation. This function has
    no Robinhood client and no execution capability by design.
    """

    violations: list[str] = []

    # This check is intentionally first and fail-closed. The validator does
    # not infer safety from an absent marker or an unavailable supervisor.
    if kill_switch_engaged is None:
        violations.append("KILL_SWITCH_STATUS_UNKNOWN")
    elif kill_switch_engaged:
        suffix = f":{kill_switch_reason}" if kill_switch_reason else ""
        violations.append(f"KILL_SWITCH_ENGAGED{suffix}")

    try:
        validate_safety_config(config)
    except UnsafeConfigurationError as error:
        violations.append(f"UNSAFE_CONFIG: {error}")

    if config.get("system_mode") not in {"SHADOW", "LIVE"}:
        violations.append("MODE_NOT_EXECUTABLE")

    if config.get("system_mode") == "LIVE":
        if config.get("live_trading_enabled") is not True:
            violations.append("LIVE_TRADING_DISABLED")
        if config.get("order_tools_enabled") is not True:
            violations.append("ORDER_TOOLS_DISABLED")
        if config.get("realtime_option_quote_verified") is not True:
            violations.append("REALTIME_OPTION_QUOTE_NOT_VERIFIED")

    if account.account_type is None:
        violations.append("ACCOUNT_TYPE_UNKNOWN")
    elif account.account_type.lower() != "cash":
        violations.append("ACCOUNT_NOT_CASH")

    equity = _decimal(account.equity)
    buying_power = _decimal(account.buying_power)
    settled_cash = _decimal(account.settled_cash)
    reserved_cash = _decimal(account.reserved_cash)

    if equity is None:
        violations.append("ACCOUNT_EQUITY_UNKNOWN")
    elif equity < Decimal(str(config.get("account_equity_kill_threshold_usd", 225))):
        violations.append("ACCOUNT_EQUITY_KILL_SWITCH")

    if buying_power is None:
        violations.append("BUYING_POWER_UNKNOWN")
    if settled_cash is None:
        violations.append("SETTLED_CASH_UNKNOWN")
    if reserved_cash is None:
        violations.append("RESERVED_CASH_UNKNOWN")
    elif reserved_cash < ZERO:
        violations.append("RESERVED_CASH_INVALID")

    position_fields = {
        "OPTION_POSITIONS_UNKNOWN": account.option_position_count,
        "EQUITY_POSITIONS_UNKNOWN": account.equity_position_count,
        "OPTION_ORDERS_UNKNOWN": account.open_option_order_count,
        "EQUITY_ORDERS_UNKNOWN": account.open_equity_order_count,
    }
    for unknown_code, value in position_fields.items():
        if value is None:
            violations.append(unknown_code)

    if account.option_position_count not in {None, 0}:
        violations.append("EXISTING_OPTION_POSITION")
    if account.equity_position_count not in {None, 0}:
        violations.append("UNRECOGNIZED_EQUITY_POSITION")
    if account.open_option_order_count not in {None, 0}:
        violations.append("EXISTING_OPTION_ORDER")
    if account.open_equity_order_count not in {None, 0}:
        violations.append("EXISTING_EQUITY_ORDER")

    if account.consecutive_losses is None:
        violations.append("CONSECUTIVE_LOSSES_UNKNOWN")
    elif account.consecutive_losses >= int(config.get("max_consecutive_losses", 3)):
        violations.append("CONSECUTIVE_LOSS_PAUSE")

    if account.entries_today is None:
        violations.append("DAILY_ENTRY_COUNT_UNKNOWN")
    elif account.entries_today >= int(config.get("max_daily_entries", 1)):
        violations.append("DAILY_ENTRY_LIMIT_REACHED")

    if market_open is not True:
        violations.append("MARKET_NOT_CONFIRMED_OPEN")
    if within_entry_window is not True:
        violations.append("OUTSIDE_ENTRY_WINDOW")
    if near_forced_exit is not False:
        violations.append("TOO_CLOSE_TO_FORCED_EXIT")

    if intent.action is not OrderAction.BUY:
        violations.append("OPENING_ACTION_MUST_BE_BUY")
    if intent.position_effect is not PositionEffect.OPEN:
        violations.append("POSITION_EFFECT_MUST_BE_OPEN")
    if intent.order_type is not OrderType.LIMIT:
        violations.append("LIMIT_ORDER_REQUIRED")
    if intent.quantity != int(config.get("max_contracts_per_position", 1)):
        violations.append("QUANTITY_MUST_EQUAL_ONE")

    allowed_position = f"LONG_{intent.option_type.value}"
    if allowed_position not in set(config.get("allowed_opening_positions", [])):
        violations.append("OPTION_DIRECTION_NOT_ALLOWED")

    limit_price = _decimal(intent.limit_price)
    premium_usd: Decimal | None = None
    if limit_price is None or limit_price <= ZERO:
        violations.append("INVALID_LIMIT_PRICE")
    else:
        premium_usd = limit_price * ONE_HUNDRED

    if completed_live_trades is None or completed_live_trades < 0:
        violations.append("COMPLETED_TRADE_COUNT_UNKNOWN")
        stage_limit = None
    else:
        approved_stage = config.get("approved_trade_stage", 1)
        if approved_stage == 1:
            stage_limit = Decimal(str(config.get("first_stage_max_premium_usd", 75)))
        elif completed_live_trades < int(config.get("first_stage_trade_count", 5)):
            violations.append("STAGE_2_REQUIRES_FIVE_COMPLETED_TRADES")
            stage_limit = Decimal(str(config.get("first_stage_max_premium_usd", 75)))
        else:
            stage_limit = Decimal(str(config.get("second_stage_max_premium_usd", 100)))

    if premium_usd is not None:
        absolute_limit = Decimal(str(config.get("absolute_max_premium_usd", 120)))
        deployable_limit = Decimal(str(config.get("max_deployable_capital_usd", 300)))

        if premium_usd > absolute_limit:
            violations.append("ABSOLUTE_PREMIUM_LIMIT_EXCEEDED")
        if stage_limit is not None and premium_usd > stage_limit:
            violations.append("CURRENT_STAGE_PREMIUM_LIMIT_EXCEEDED")
        if premium_usd > deployable_limit:
            violations.append("DEPLOYABLE_CAPITAL_LIMIT_EXCEEDED")
        if buying_power is not None and premium_usd > buying_power:
            violations.append("INSUFFICIENT_BUYING_POWER")
        if settled_cash is not None and reserved_cash is not None:
            available_settled_cash = settled_cash - reserved_cash
            if available_settled_cash < ZERO:
                violations.append("CASH_RESERVATIONS_EXCEED_SETTLED_CASH")
            elif premium_usd > available_settled_cash:
                violations.append("INSUFFICIENT_SETTLED_CASH")

    if now.tzinfo is None or now.utcoffset() is None:
        violations.append("CURRENT_TIME_NOT_TIMEZONE_AWARE")
        now_utc = None
    else:
        now_utc = now.astimezone(timezone.utc)

    market_date = now.astimezone(ZoneInfo("America/New_York")).date()
    dte = (intent.expiration - market_date).days
    if dte < int(config.get("minimum_dte", 7)):
        violations.append("DTE_BELOW_MINIMUM")
    if dte > int(config.get("maximum_dte", 21)):
        violations.append("DTE_ABOVE_MAXIMUM")

    bid = _decimal(intent.bid)
    ask = _decimal(intent.ask)
    if bid is None or ask is None:
        violations.append("OPTION_QUOTE_MISSING")
    elif bid < ZERO or ask <= ZERO or ask < bid:
        violations.append("OPTION_QUOTE_INVALID")
    else:
        mid = (bid + ask) / Decimal("2")
        relative_spread = (ask - bid) / mid if mid > ZERO else None
        if max_relative_spread is None:
            violations.append("MAX_RELATIVE_SPREAD_NOT_CONFIGURED")
        elif relative_spread is None or relative_spread > max_relative_spread:
            violations.append("OPTION_SPREAD_TOO_WIDE")

    if intent.quote_updated_at is None:
        violations.append("OPTION_QUOTE_TIMESTAMP_MISSING")
    elif intent.quote_updated_at.tzinfo is None or intent.quote_updated_at.utcoffset() is None:
        violations.append("OPTION_QUOTE_TIMESTAMP_NOT_TIMEZONE_AWARE")
    elif now_utc is not None:
        quote_age_seconds = (
            now_utc - intent.quote_updated_at.astimezone(timezone.utc)
        ).total_seconds()
        if quote_age_seconds < -5:
            violations.append("OPTION_QUOTE_TIMESTAMP_IN_FUTURE")
        if max_quote_age_seconds is None:
            violations.append("MAX_QUOTE_AGE_NOT_CONFIGURED")
        elif quote_age_seconds > max_quote_age_seconds:
            violations.append("OPTION_QUOTE_STALE")

    if minimum_option_volume is None:
        violations.append("MINIMUM_OPTION_VOLUME_NOT_CONFIGURED")
    elif intent.volume is None:
        violations.append("OPTION_VOLUME_UNKNOWN")
    elif intent.volume < minimum_option_volume:
        violations.append("OPTION_VOLUME_TOO_LOW")

    if minimum_open_interest is None:
        violations.append("MINIMUM_OPEN_INTEREST_NOT_CONFIGURED")
    elif intent.open_interest is None:
        violations.append("OPEN_INTEREST_UNKNOWN")
    elif intent.open_interest < minimum_open_interest:
        violations.append("OPEN_INTEREST_TOO_LOW")

    unique_violations = tuple(dict.fromkeys(violations))
    return RiskDecision(
        approved=not unique_violations,
        violations=unique_violations,
        estimated_premium_usd=premium_usd,
    )

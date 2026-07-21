from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from journal.writer import FORBIDDEN_KEY_FRAGMENTS
from risk.models import AccountSnapshot, OptionType
from strategy.market_regime import CompletedMarketBar
from strategy.shadow_pipeline import OptionCandidate
from strategy.shadow_runner import ShadowSnapshot
from strategy.underlying_signal import UnderlyingSignalSnapshot


class InvalidShadowInputError(ValueError):
    pass


def _forbidden_paths(value: Any, path: str = "input") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                found.append(f"{path}.{key}")
            found.extend(_forbidden_paths(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_paths(child, f"{path}[{index}]"))
    return found


def _required(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise InvalidShadowInputError(f"Missing required field: {path}.{key}")
    return mapping[key]


def _decimal(value: Any, path: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidShadowInputError(f"Invalid decimal: {path}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidShadowInputError(f"Invalid decimal: {path}") from error


def _integer(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidShadowInputError(f"Invalid integer: {path}")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise InvalidShadowInputError(f"Invalid integer: {path}") from error


def _boolean(value: Any, path: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise InvalidShadowInputError(f"Invalid boolean: {path}")
    return value


def _datetime(value: Any, path: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidShadowInputError(f"Invalid datetime: {path}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise InvalidShadowInputError(f"Invalid datetime: {path}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidShadowInputError(f"Datetime must include timezone: {path}")
    return parsed


def _date(value: Any, path: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidShadowInputError(f"Invalid date: {path}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidShadowInputError(f"Invalid date: {path}") from error


def load_shadow_input(path: str | Path) -> tuple[str, datetime, ShadowSnapshot]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidShadowInputError(f"Cannot read valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise InvalidShadowInputError("Shadow input root must be an object.")
    forbidden = _forbidden_paths(raw)
    if forbidden:
        raise InvalidShadowInputError("Sensitive fields are forbidden: " + ", ".join(forbidden))
    if raw.get("schema_version") != 1:
        raise InvalidShadowInputError("schema_version must equal 1.")

    sample_id = str(_required(raw, "sample_id", "input"))
    if not sample_id.strip():
        raise InvalidShadowInputError("sample_id cannot be empty.")
    received_at = _datetime(_required(raw, "received_at", "input"), "input.received_at")
    assert received_at is not None

    account_raw = _required(raw, "account", "input")
    market_raw = _required(raw, "market", "input")
    underlying_raw = _required(raw, "underlying", "input")
    option_raw = _required(raw, "option", "input")
    bars_raw = _required(raw, "market_bars", "input")
    if not all(isinstance(item, dict) for item in (account_raw, market_raw, underlying_raw, option_raw)):
        raise InvalidShadowInputError("account, market, underlying and option must be objects.")
    if not isinstance(bars_raw, list):
        raise InvalidShadowInputError("market_bars must be an array.")
    if not all(isinstance(bar, dict) for bar in bars_raw):
        raise InvalidShadowInputError("Every market_bars item must be an object.")

    account = AccountSnapshot(
        str(_required(account_raw, "account_type", "input.account")),
        _decimal(_required(account_raw, "equity", "input.account"), "input.account.equity"),
        _decimal(_required(account_raw, "buying_power", "input.account"), "input.account.buying_power"),
        _integer(_required(account_raw, "option_position_count", "input.account"), "input.account.option_position_count"),
        _integer(_required(account_raw, "equity_position_count", "input.account"), "input.account.equity_position_count"),
        _integer(_required(account_raw, "open_option_order_count", "input.account"), "input.account.open_option_order_count"),
        _integer(_required(account_raw, "open_equity_order_count", "input.account"), "input.account.open_equity_order_count"),
        _integer(_required(account_raw, "consecutive_losses", "input.account"), "input.account.consecutive_losses"),
        _integer(_required(account_raw, "entries_today", "input.account"), "input.account.entries_today"),
        _decimal(_required(account_raw, "settled_cash", "input.account"), "input.account.settled_cash"),
        _decimal(_required(account_raw, "unsettled_cash", "input.account"), "input.account.unsettled_cash"),
        _decimal(_required(account_raw, "reserved_cash", "input.account"), "input.account.reserved_cash"),
    )
    bars = tuple(
        CompletedMarketBar(
            str(_required(bar, "symbol", f"input.market_bars[{index}]")),
            _decimal(_required(bar, "close", f"input.market_bars[{index}]"), f"input.market_bars[{index}].close"),
            _decimal(_required(bar, "vwap", f"input.market_bars[{index}]"), f"input.market_bars[{index}].vwap"),
            _decimal(_required(bar, "ema_fast", f"input.market_bars[{index}]"), f"input.market_bars[{index}].ema_fast"),
            _decimal(_required(bar, "ema_slow", f"input.market_bars[{index}]"), f"input.market_bars[{index}].ema_slow"),
            _integer(_required(bar, "interval_minutes", f"input.market_bars[{index}]"), f"input.market_bars[{index}].interval_minutes"),
            _datetime(_required(bar, "started_at", f"input.market_bars[{index}]"), f"input.market_bars[{index}].started_at"),
            _datetime(_required(bar, "ended_at", f"input.market_bars[{index}]"), f"input.market_bars[{index}].ended_at"),
            _datetime(_required(bar, "source_updated_at", f"input.market_bars[{index}]"), f"input.market_bars[{index}].source_updated_at"),
            _datetime(_required(bar, "received_at", f"input.market_bars[{index}]"), f"input.market_bars[{index}].received_at"),
            _boolean(_required(bar, "completed", f"input.market_bars[{index}]"), f"input.market_bars[{index}].completed"),
        )
        for index, bar in enumerate(bars_raw)
        if isinstance(bar, dict)
    )
    underlying = UnderlyingSignalSnapshot(
        str(_required(underlying_raw, "symbol", "input.underlying")),
        _decimal(_required(underlying_raw, "close", "input.underlying"), "input.underlying.close"),
        _decimal(_required(underlying_raw, "vwap", "input.underlying"), "input.underlying.vwap"),
        _decimal(_required(underlying_raw, "ema_fast", "input.underlying"), "input.underlying.ema_fast"),
        _decimal(_required(underlying_raw, "ema_slow", "input.underlying"), "input.underlying.ema_slow"),
        _decimal(_required(underlying_raw, "breakout_high", "input.underlying"), "input.underlying.breakout_high"),
        _decimal(_required(underlying_raw, "breakdown_low", "input.underlying"), "input.underlying.breakdown_low"),
        _integer(_required(underlying_raw, "current_volume", "input.underlying"), "input.underlying.current_volume"),
        _decimal(_required(underlying_raw, "average_volume", "input.underlying"), "input.underlying.average_volume"),
    )
    option_type_raw = str(_required(option_raw, "option_type", "input.option")).upper()
    if option_type_raw not in {"CALL", "PUT"}:
        raise InvalidShadowInputError("input.option.option_type must be CALL or PUT.")
    option = OptionCandidate(
        str(_required(option_raw, "underlying", "input.option")),
        OptionType(option_type_raw),
        _decimal(_required(option_raw, "strike", "input.option"), "input.option.strike") or Decimal("-1"),
        _date(_required(option_raw, "expiration", "input.option"), "input.option.expiration") or date.min,
        _decimal(_required(option_raw, "bid", "input.option"), "input.option.bid"),
        _decimal(_required(option_raw, "ask", "input.option"), "input.option.ask"),
        _decimal(_required(option_raw, "delta", "input.option"), "input.option.delta"),
        _datetime(_required(option_raw, "quote_updated_at", "input.option"), "input.option.quote_updated_at"),
        _integer(_required(option_raw, "volume", "input.option"), "input.option.volume"),
        _integer(_required(option_raw, "open_interest", "input.option"), "input.option.open_interest"),
        _date(_required(option_raw, "earnings_date", "input.option"), "input.option.earnings_date"),
        _datetime(_required(option_raw, "quote_received_at", "input.option"), "input.option.quote_received_at"),
    )
    snapshot = ShadowSnapshot(
        received_at,
        bars,
        underlying,
        option,
        account,
        _integer(_required(raw, "completed_live_trades", "input"), "input.completed_live_trades"),
        _boolean(_required(market_raw, "market_open", "input.market"), "input.market.market_open"),
        _boolean(_required(market_raw, "within_entry_window", "input.market"), "input.market.within_entry_window"),
        _boolean(_required(market_raw, "near_forced_exit", "input.market"), "input.market.near_forced_exit"),
    )
    source_updated_at = _datetime(
        _required(raw, "source_updated_at", "input"), "input.source_updated_at"
    )
    assert source_updated_at is not None
    return sample_id, source_updated_at, snapshot

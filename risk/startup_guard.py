from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


HARD_MAX_DEPLOYABLE_CAPITAL_USD = 300
HARD_FIRST_STAGE_MAX_PREMIUM_USD = 75
HARD_ABSOLUTE_MAX_PREMIUM_USD = 120
HARD_MAX_CONTRACTS = 1
HARD_MAX_CONCURRENT_POSITIONS = 1
HARD_MAX_DAILY_ENTRIES = 1
HARD_EQUITY_KILL_THRESHOLD_USD = 225
HARD_MAX_CONSECUTIVE_LOSSES = 3
HARD_MINIMUM_DTE = 7
HARD_MAXIMUM_DTE = 21
HARD_ALLOWED_OPENING_POSITIONS = {"LONG_CALL", "LONG_PUT"}


class UnsafeConfigurationError(RuntimeError):
    """Raised when configuration attempts to weaken a hard safety rule."""


def load_safety_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)

    if not config_path.is_file():
        raise UnsafeConfigurationError(
            f"Safety configuration not found: {config_path}"
        )

    try:
        with config_path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise UnsafeConfigurationError(
            f"Safety configuration cannot be read: {error}"
        ) from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UnsafeConfigurationError(message)


def validate_safety_config(config: dict[str, Any]) -> None:
    require(config.get("schema_version") == 1, "Unsupported schema version.")

    require(
        config.get("max_deployable_capital_usd", float("inf"))
        <= HARD_MAX_DEPLOYABLE_CAPITAL_USD,
        "Deployable capital exceeds the hard $300 limit.",
    )
    require(
        config.get("initial_risk_capital_usd") == HARD_MAX_DEPLOYABLE_CAPITAL_USD,
        "Initial risk capital must remain exactly $300.",
    )
    require(
        config.get("max_deployable_capital_usd")
        <= config.get("initial_risk_capital_usd"),
        "Deployable capital cannot exceed initial risk capital.",
    )
    require(
        config.get("first_stage_trade_count") == 5,
        "The first stage must remain exactly 5 completed trades.",
    )
    require(
        config.get("first_stage_max_premium_usd", float("inf"))
        <= HARD_FIRST_STAGE_MAX_PREMIUM_USD,
        "First-stage premium exceeds the hard $75 limit.",
    )
    require(
        config.get("second_stage_max_premium_usd", float("inf"))
        <= HARD_ABSOLUTE_MAX_PREMIUM_USD,
        "Second-stage premium exceeds the hard $120 limit.",
    )
    require(
        config.get("absolute_max_premium_usd", float("inf"))
        <= HARD_ABSOLUTE_MAX_PREMIUM_USD,
        "Premium exceeds the hard $120 limit.",
    )
    require(
        config.get("max_contracts_per_position") == HARD_MAX_CONTRACTS,
        "Contract quantity must remain exactly 1.",
    )
    require(
        config.get("max_concurrent_positions")
        == HARD_MAX_CONCURRENT_POSITIONS,
        "Concurrent positions must remain exactly 1.",
    )
    require(
        config.get("max_daily_entries") == HARD_MAX_DAILY_ENTRIES,
        "Daily entries must remain exactly 1.",
    )
    require(
        config.get("account_equity_kill_threshold_usd")
        >= HARD_EQUITY_KILL_THRESHOLD_USD,
        "Equity kill threshold cannot be below $225.",
    )
    require(
        config.get("max_consecutive_losses")
        <= HARD_MAX_CONSECUTIVE_LOSSES,
        "Consecutive-loss limit cannot exceed 3.",
    )
    require(
        config.get("minimum_dte") >= HARD_MINIMUM_DTE,
        "Minimum DTE cannot be below 7.",
    )
    require(
        config.get("maximum_dte") <= HARD_MAXIMUM_DTE,
        "Maximum DTE cannot exceed 21.",
    )

    allowed_positions = set(config.get("allowed_opening_positions", []))
    require(
        allowed_positions <= HARD_ALLOWED_OPENING_POSITIONS,
        "Only LONG_CALL and LONG_PUT may be allowed.",
    )
    require(bool(allowed_positions), "At least one safe position type is required.")

    require(config.get("allow_margin") is False, "Margin must remain disabled.")
    require(
        config.get("allow_short_options") is False,
        "Short options must remain disabled.",
    )
    require(
        config.get("allow_multi_leg_options") is False,
        "Multi-leg options must remain disabled.",
    )
    require(
        config.get("allow_stock_shorting") is False,
        "Stock shorting must remain disabled.",
    )
    require(
        config.get("allow_averaging_down") is False,
        "Averaging down must remain disabled.",
    )
    require(
        config.get("allow_martingale") is False,
        "Martingale must remain disabled.",
    )
    require(
        config.get("allow_overnight") is False,
        "Overnight holding must remain disabled.",
    )
    require(config.get("allow_0dte") is False, "0DTE must remain disabled.")
    require(
        config.get("market_timezone") == "America/New_York",
        "Market timezone must remain America/New_York.",
    )
    entry_delay = config.get("entry_delay_after_open_minutes")
    stop_before_close = config.get("stop_new_entries_before_close_minutes")
    force_exit = config.get("force_exit_before_close_minutes")
    emergency_exit = config.get("emergency_exit_before_close_minutes")
    require(
        isinstance(entry_delay, int) and entry_delay >= 30,
        "New entries must wait at least 30 minutes after the open.",
    )
    require(
        isinstance(stop_before_close, int) and 60 <= stop_before_close <= 90,
        "New entries must stop 60 to 90 minutes before the close.",
    )
    require(
        isinstance(force_exit, int) and force_exit >= 30,
        "Forced exits must begin at least 30 minutes before the close.",
    )
    require(
        isinstance(emergency_exit, int) and emergency_exit >= 15,
        "Emergency exits must begin at least 15 minutes before the close.",
    )
    require(
        stop_before_close > force_exit > emergency_exit,
        "Session cutoffs must be ordered: stop entries, force exit, emergency exit.",
    )
    require(
        config.get("allow_profit_reinvestment") is False,
        "Profit reinvestment must remain disabled.",
    )
    require(
        config.get("allow_automatic_transfers") is False,
        "Automatic transfers must remain disabled.",
    )
    require(
        config.get("stage_upgrade_requires_manual_approval") is True,
        "Stage upgrades must require manual approval.",
    )

    approved_stage = config.get("approved_trade_stage")
    require(approved_stage in {1, 2}, "Approved trade stage must be 1 or 2.")
    if approved_stage == 2:
        require(
            config.get("stage_2_manually_approved") is True,
            "Stage 2 requires explicit manual approval.",
        )
        require(
            config.get("stage_2_integrity_checks_passed") is True,
            "Stage 2 requires completed integrity checks.",
        )

    live_enabled = config.get("live_trading_enabled") is True
    order_tools_enabled = config.get("order_tools_enabled") is True
    quote_verified = config.get("realtime_option_quote_verified") is True

    if live_enabled or order_tools_enabled:
        require(
            config.get("system_mode") == "LIVE",
            "Live capabilities require system_mode LIVE.",
        )
        require(
            quote_verified,
            "Live capabilities require verified realtime option quotes.",
        )


def validate_file(path: str | Path = "config/safety.toml") -> None:
    validate_safety_config(load_safety_config(path))


if __name__ == "__main__":
    validate_file()
    print("SAFETY CONFIG VALID")

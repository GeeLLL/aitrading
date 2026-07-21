from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monitoring.kill_switch import KillSwitch
from monitoring.shadow_activation import REQUIRED_P0_CHECKS, load_shadow_authorization
from risk.startup_guard import load_safety_config, validate_safety_config
from strategy.policy import load_strategy_policy, validate_strategy_policy


MONDAY_MARKET_CHECKS = (
    "official_raw_mcp_snapshot",
    "raw_to_feature_reproducibility",
    "official_instrument_session",
    "official_account_cash_reconciliation",
    "official_orders_positions_reconciliation",
    "fresh_option_quote",
)

REQUIRED_LOCAL_FILES = (
    "config/safety.toml",
    "strategy/strategy_v1.0.toml",
    "config/raw_mcp_snapshot.schema.json",
    "prompts/robinhood_raw_collector.md",
    "execution/raw_data_vault.py",
    "execution/execution_guard.py",
    "execution/order_state.py",
    "execution/broker_reconciliation.py",
    "execution/advanced_friction.py",
    "monitoring/startup_reconciliation.py",
    "risk/cash_ledger.py",
    "risk/cash_reconciliation.py",
    "strategy/deterministic_indicators.py",
    "strategy/option_surface.py",
    "research/baselines.py",
    "research/statistical_evidence.py",
    "research/walk_forward.py",
    "config/parameter_evidence.example.json",
)


@dataclass(frozen=True)
class ReadinessReport:
    offline_ready: bool
    formal_shadow_authorized: bool
    monday_go: bool
    passed: tuple[str, ...]
    blockers: tuple[str, ...]
    pending_market_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "offline_ready": self.offline_ready,
            "formal_shadow_authorized": self.formal_shadow_authorized,
            "monday_go": self.monday_go,
            "passed": list(self.passed),
            "blockers": list(self.blockers),
            "pending_market_checks": list(self.pending_market_checks),
            "safety_note": "This report never activates Shadow or enables order tools.",
        }


def _valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return True


def build_shadow_readiness(
    *,
    root: str | Path = ".",
    market_checks: dict[str, bool] | None = None,
) -> ReadinessReport:
    project = Path(root)
    passed: list[str] = []
    blockers: list[str] = []

    try:
        safety = load_safety_config(project / "config/safety.toml")
        validate_safety_config(safety)
        passed.append("SAFETY_CONFIG_VALID")
    except Exception as error:  # readiness must report rather than crash
        safety = {}
        blockers.append(f"SAFETY_CONFIG_INVALID:{type(error).__name__}")

    if safety.get("system_mode") == "READ_ONLY":
        passed.append("SYSTEM_READ_ONLY")
    else:
        blockers.append("SYSTEM_NOT_READ_ONLY")
    if safety.get("live_trading_enabled") is False:
        passed.append("LIVE_TRADING_DISABLED")
    else:
        blockers.append("LIVE_TRADING_NOT_DISABLED")
    if safety.get("order_tools_enabled") is False:
        passed.append("ORDER_TOOLS_DISABLED")
    else:
        blockers.append("ORDER_TOOLS_NOT_DISABLED")

    kill = KillSwitch(project / "state/TRADING_ARMED").status()
    if kill.engaged:
        passed.append("KILL_SWITCH_ENGAGED")
    else:
        blockers.append("KILL_SWITCH_NOT_ENGAGED")

    try:
        policy = load_strategy_policy(project / "strategy/strategy_v1.0.toml")
        validate_strategy_policy(policy)
        passed.append("STRATEGY_POLICY_VALID")
    except Exception as error:
        policy = {}
        blockers.append(f"STRATEGY_POLICY_INVALID:{type(error).__name__}")

    if policy.get("status") == "DESIGN":
        passed.append("STRATEGY_REMAINS_DESIGN")
    else:
        blockers.append("UNEXPECTED_STRATEGY_STATUS")

    for relative in REQUIRED_LOCAL_FILES:
        if (project / relative).is_file():
            passed.append(f"FILE_PRESENT:{relative}")
        else:
            blockers.append(f"FILE_MISSING:{relative}")
    for relative in ("config/raw_mcp_snapshot.schema.json", "config/parameter_evidence.example.json"):
        if not _valid_json(project / relative):
            blockers.append(f"JSON_INVALID:{relative}")

    strategy_version = str(policy.get("strategy_version") or "")
    authorized = load_shadow_authorization(
        strategy_version,
        project / "state/shadow_authorization.json",
    )
    checks = market_checks or {}
    pending = tuple(check for check in MONDAY_MARKET_CHECKS if checks.get(check) is not True)

    # Formal authorization is intentionally not created here. All P0 checks and
    # explicit owner approval are handled by monitoring.shadow_activation.
    offline_ready = not blockers
    monday_go = offline_ready and authorized and not pending
    if not authorized:
        blockers.append("FORMAL_SHADOW_NOT_AUTHORIZED")
    if pending:
        blockers.extend(f"MARKET_CHECK_PENDING:{check}" for check in pending)

    return ReadinessReport(
        offline_ready,
        authorized,
        monday_go,
        tuple(passed),
        tuple(blockers),
        pending,
    )

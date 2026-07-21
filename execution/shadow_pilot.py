from __future__ import annotations

from pathlib import Path
from typing import Any

from execution.shadow_input import load_shadow_input
from journal.shadow_recorder import ShadowSessionRecorder
from risk.startup_guard import load_safety_config, validate_safety_config
from strategy.policy import load_strategy_policy, validate_strategy_policy
from strategy.shadow_runner import ShadowRunner
from strategy.shadow_session import ShadowSessionController


def run_one_shot_pilot(
    input_path: str | Path,
    *,
    log_root: str | Path = "logs/shadow",
) -> dict[str, Any]:
    """Run one explicit, read-only pilot decision and close it without a fill."""

    sample_id, source_updated_at, snapshot = load_shadow_input(input_path)
    safety = load_safety_config("config/safety.toml")
    validate_safety_config(safety)
    policy = load_strategy_policy()
    validate_strategy_policy(policy)
    recorder = ShadowSessionRecorder(
        strategy_version=policy["strategy_version"],
        session_date=snapshot.observed_at.date(),
        root=Path(log_root),
    )
    runner = ShadowRunner(
        recorder=recorder,
        safety_config=safety,
        strategy_policy=policy,
        pilot_mode=True,
    )
    controller = ShadowSessionController(runner)
    controller.start()
    decision = controller.process_decision_snapshot(
        sample_id=sample_id,
        source_updated_at=source_updated_at,
        received_at=snapshot.observed_at,
        snapshot=snapshot,
    )
    if decision is None:
        return {
            "status": "DATA_REJECTED",
            "session_state": recorder.state.value,
            "simulation_only": True,
            "log_path": str(recorder.path),
        }
    controller.finish_day(reason="ONE_SHOT_PILOT_COMPLETED")
    return {
        "status": decision.status.value,
        "reasons": list(decision.reasons),
        "market_regime": decision.market_regime,
        "signal_direction": decision.signal_direction,
        "proposed_limit_price": (
            str(decision.proposed_limit_price)
            if decision.proposed_limit_price is not None
            else None
        ),
        "estimated_premium_usd": (
            str(decision.estimated_premium_usd)
            if decision.estimated_premium_usd is not None
            else None
        ),
        "session_state": recorder.state.value,
        "simulation_only": True,
        "assumed_fill": False,
        "log_path": str(recorder.path),
    }


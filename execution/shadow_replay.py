from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from execution.shadow_input import InvalidShadowInputError, load_shadow_input
from journal.shadow_recorder import ShadowSessionRecorder
from risk.startup_guard import load_safety_config, validate_safety_config
from strategy.policy import load_strategy_policy, validate_strategy_policy
from strategy.shadow_runner import PositionObservation, ShadowRunner
from strategy.shadow_session import ShadowSessionController


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidShadowInputError(f"{field} must be a datetime string.")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise InvalidShadowInputError(f"Invalid datetime: {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidShadowInputError(f"Datetime must include timezone: {field}")
    return parsed


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidShadowInputError(f"Invalid decimal: {field}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidShadowInputError(f"Invalid decimal: {field}") from error


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidShadowInputError(f"{field} must be boolean.")
    return value


def run_shadow_replay(
    scenario_path: str | Path,
    *,
    log_root: str | Path = "logs/shadow",
) -> dict[str, Any]:
    path = Path(scenario_path)
    try:
        scenario = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidShadowInputError(f"Cannot read replay scenario: {error}") from error
    if not isinstance(scenario, dict) or scenario.get("schema_version") != 1:
        raise InvalidShadowInputError("Replay schema_version must equal 1.")
    snapshot_name = scenario.get("decision_input")
    if not isinstance(snapshot_name, str):
        raise InvalidShadowInputError("decision_input must be a path string.")
    snapshot_path = (path.parent / snapshot_name).resolve()
    sample_id, source_updated_at, snapshot = load_shadow_input(snapshot_path)

    safety = load_safety_config("config/safety.toml")
    validate_safety_config(safety)
    policy = load_strategy_policy()
    validate_strategy_policy(policy)
    recorder = ShadowSessionRecorder(
        policy["strategy_version"], snapshot.observed_at.date(), root=Path(log_root)
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
        return {"status": "DATA_REJECTED", "session_state": recorder.state.value, "log_path": str(recorder.path)}

    for index, quote in enumerate(scenario.get("entry_quotes") or []):
        if recorder.state.value != "ENTRY_PENDING":
            break
        if not isinstance(quote, dict):
            raise InvalidShadowInputError(f"entry_quotes[{index}] must be an object.")
        controller.process_entry_quote(
            sample_id=str(quote.get("sample_id") or ""),
            quote_updated_at=_time(quote.get("quote_updated_at"), f"entry_quotes[{index}].quote_updated_at"),
            received_at=_time(quote.get("received_at"), f"entry_quotes[{index}].received_at"),
            observed_ask=_decimal(quote.get("ask"), f"entry_quotes[{index}].ask"),
            timed_out=_bool(quote.get("timed_out"), f"entry_quotes[{index}].timed_out"),
        )

    for index, item in enumerate(scenario.get("position_observations") or []):
        if recorder.state.value != "POSITION_OPEN":
            break
        if not isinstance(item, dict):
            raise InvalidShadowInputError(f"position_observations[{index}] must be an object.")
        observation = PositionObservation(
            observed_at=_time(item.get("observed_at"), f"position_observations[{index}].observed_at"),
            quote_updated_at=_time(item.get("quote_updated_at"), f"position_observations[{index}].quote_updated_at"),
            current_bid=_decimal(item.get("bid"), f"position_observations[{index}].bid"),
            current_mark=_decimal(item.get("mark"), f"position_observations[{index}].mark"),
            underlying_vwap_still_valid=(
                item.get("underlying_vwap_still_valid")
                if item.get("underlying_vwap_still_valid") is None
                else _bool(item.get("underlying_vwap_still_valid"), f"position_observations[{index}].underlying_vwap_still_valid")
            ),
            force_exit_due=_bool(item.get("force_exit_due"), f"position_observations[{index}].force_exit_due"),
            emergency_exit_due=_bool(item.get("emergency_exit_due"), f"position_observations[{index}].emergency_exit_due"),
        )
        controller.process_position_observation(
            sample_id=str(item.get("sample_id") or ""), observation=observation
        )

    if _bool(scenario.get("finish_day"), "finish_day"):
        controller.finish_day(reason="REPLAY_SCENARIO_COMPLETED")
    return {
        "status": decision.status.value,
        "session_state": recorder.state.value,
        "simulation_only": True,
        "strategy_evidence_eligible": False,
        "log_path": str(recorder.path),
    }


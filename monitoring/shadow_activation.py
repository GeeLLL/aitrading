from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


REQUIRED_P0_CHECKS = (
    "kill_switch_boundary",
    "unknown_contract_fail_closed",
    "trusted_data_plane",
    "bar_time_integrity",
    "durable_state_reconciliation",
    "cash_settlement_ledger",
    "experiment_reporting_integrity",
    "instrument_session_integrity",
)


@dataclass(frozen=True)
class ShadowAuthorization:
    authorized: bool
    strategy_version: str
    approved_at: str | None
    reasons: tuple[str, ...]


def evaluate_shadow_authorization(
    *,
    strategy_version: str,
    p0_checks: Mapping[str, bool],
    owner_approved: bool,
) -> ShadowAuthorization:
    reasons: list[str] = []
    if not strategy_version:
        reasons.append("STRATEGY_VERSION_UNKNOWN")
    for check in REQUIRED_P0_CHECKS:
        if p0_checks.get(check) is not True:
            reasons.append(f"P0_CHECK_INCOMPLETE:{check}")
    if not owner_approved:
        reasons.append("OWNER_APPROVAL_REQUIRED")
    return ShadowAuthorization(
        not reasons,
        strategy_version,
        datetime.now(timezone.utc).isoformat() if not reasons else None,
        tuple(reasons),
    )


def persist_shadow_authorization(
    authorization: ShadowAuthorization,
    path: str | Path = "state/shadow_authorization.json",
) -> Path:
    if not authorization.authorized:
        raise ValueError("Cannot persist an unauthorized Shadow transition")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(".tmp")
    payload = {
        "authorized": True,
        "strategy_version": authorization.strategy_version,
        "approved_at": authorization.approved_at,
    }
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, destination)
    return destination


def load_shadow_authorization(
    strategy_version: str,
    path: str | Path = "state/shadow_authorization.json",
) -> bool:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return raw.get("authorized") is True and raw.get("strategy_version") == strategy_version


def load_p0_qualification(path: str | Path, strategy_version: str) -> dict[str, bool]:
    """Load evidence-bearing P0 results; unsupported claims fail closed."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("P0_QUALIFICATION_INVALID") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("P0_QUALIFICATION_SCHEMA_INVALID")
    if raw.get("strategy_version") != strategy_version:
        raise ValueError("P0_QUALIFICATION_STRATEGY_MISMATCH")
    checks_raw = raw.get("checks")
    if not isinstance(checks_raw, dict) or set(checks_raw) != set(REQUIRED_P0_CHECKS):
        raise ValueError("P0_QUALIFICATION_CHECK_SET_INVALID")
    result: dict[str, bool] = {}
    for name in REQUIRED_P0_CHECKS:
        check = checks_raw[name]
        if not isinstance(check, dict):
            raise ValueError(f"P0_CHECK_INVALID:{name}")
        passed = check.get("passed") is True
        evidence = check.get("evidence")
        if passed and (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) and item.strip() for item in evidence)
        ):
            raise ValueError(f"P0_CHECK_EVIDENCE_MISSING:{name}")
        result[name] = passed
    return result

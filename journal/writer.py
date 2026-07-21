from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


REQUIRED_TRADE_FIELDS = (
    "trade_id",
    "strategy_version",
    "timestamp",
    "underlying",
    "option_type",
    "strike",
    "expiration",
    "quantity",
    "bid",
    "ask",
    "mid",
    "limit_price",
    "filled_price",
    "entry_reason",
    "market_context",
    "stop_condition",
    "profit_target",
    "exit_reason",
    "exit_price",
    "gross_pnl",
    "estimated_slippage",
    "net_pnl",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "rule_violations",
    "system_errors",
)

FORBIDDEN_KEY_FRAGMENTS = (
    "account_number",
    "rhs_account",
    "password",
    "token",
    "authorization",
    "oauth",
    "credential",
    "recovery_code",
)


class JournalValidationError(ValueError):
    """Raised when a journal record is incomplete or contains secrets."""


def _find_forbidden_keys(value: Any, path: str = "record") -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                violations.append(f"{path}.{key}")
            violations.extend(_find_forbidden_keys(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            violations.extend(_find_forbidden_keys(child, f"{path}[{index}]"))
    return violations


def validate_trade_record(record: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_TRADE_FIELDS if field not in record]
    if missing:
        raise JournalValidationError(f"Missing required fields: {', '.join(missing)}")

    forbidden = _find_forbidden_keys(record)
    if forbidden:
        raise JournalValidationError(
            f"Sensitive fields are forbidden in journal records: {', '.join(forbidden)}"
        )

    if record["quantity"] != 1:
        raise JournalValidationError("Journal quantity must equal 1.")
    if record["option_type"] not in {"CALL", "PUT"}:
        raise JournalValidationError("Journal option_type must be CALL or PUT.")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported journal value: {type(value).__name__}")


def append_trade_record(
    record: Mapping[str, Any],
    path: str | Path = "logs/trades.jsonl",
) -> Path:
    validate_trade_record(record)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    envelope = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "record": dict(record),
    }
    serialized = json.dumps(
        envelope,
        default=_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    with output_path.open("a", encoding="utf-8") as file:
        file.write(serialized + "\n")
    return output_path

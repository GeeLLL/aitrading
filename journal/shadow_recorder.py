from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from journal.writer import FORBIDDEN_KEY_FRAGMENTS, JournalValidationError


class ShadowEventType(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    DECISION_RECORDED = "DECISION_RECORDED"
    ENTRY_WORKING = "ENTRY_WORKING"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    POSITION_SNAPSHOT = "POSITION_SNAPSHOT"
    EXITED = "EXITED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_ERROR = "SESSION_ERROR"


class ShadowSessionState(str, Enum):
    NEW = "NEW"
    OBSERVING = "OBSERVING"
    ENTRY_PENDING = "ENTRY_PENDING"
    POSITION_OPEN = "POSITION_OPEN"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported shadow value: {type(value).__name__}")


def _sensitive_paths(value: Any, path: str = "payload") -> list[str]:
    matches: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                matches.append(f"{path}.{key}")
            matches.extend(_sensitive_paths(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            matches.extend(_sensitive_paths(child, f"{path}[{index}]"))
    return matches


@dataclass
class ShadowSessionRecorder:
    strategy_version: str
    session_date: date
    root: Path = Path("logs/shadow")
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: ShadowSessionState = ShadowSessionState.NEW
    sequence: int = 0

    @property
    def path(self) -> Path:
        return self.root / self.session_date.isoformat() / f"{self.session_id}.jsonl"

    def _transition(self, event_type: ShadowEventType) -> ShadowSessionState:
        allowed: dict[ShadowSessionState, dict[ShadowEventType, ShadowSessionState]] = {
            ShadowSessionState.NEW: {
                ShadowEventType.SESSION_STARTED: ShadowSessionState.OBSERVING,
                ShadowEventType.SESSION_ERROR: ShadowSessionState.ERROR,
            },
            ShadowSessionState.OBSERVING: {
                ShadowEventType.DECISION_RECORDED: ShadowSessionState.OBSERVING,
                ShadowEventType.ENTRY_WORKING: ShadowSessionState.ENTRY_PENDING,
                ShadowEventType.SESSION_COMPLETED: ShadowSessionState.COMPLETE,
                ShadowEventType.SESSION_ERROR: ShadowSessionState.ERROR,
            },
            ShadowSessionState.ENTRY_PENDING: {
                ShadowEventType.ENTRY_FILLED: ShadowSessionState.POSITION_OPEN,
                ShadowEventType.ENTRY_UNFILLED: ShadowSessionState.COMPLETE,
                ShadowEventType.SESSION_ERROR: ShadowSessionState.ERROR,
            },
            ShadowSessionState.POSITION_OPEN: {
                ShadowEventType.POSITION_SNAPSHOT: ShadowSessionState.POSITION_OPEN,
                ShadowEventType.EXITED: ShadowSessionState.COMPLETE,
                ShadowEventType.SESSION_ERROR: ShadowSessionState.ERROR,
            },
            ShadowSessionState.COMPLETE: {},
            ShadowSessionState.ERROR: {},
        }
        if event_type not in allowed[self.state]:
            raise JournalValidationError(
                f"Invalid shadow event {event_type.value} while session is {self.state.value}."
            )
        return allowed[self.state][event_type]

    def append(self, event_type: ShadowEventType, payload: Mapping[str, Any]) -> Path:
        sensitive = _sensitive_paths(payload)
        if sensitive:
            raise JournalValidationError(
                "Sensitive fields are forbidden in shadow logs: " + ", ".join(sensitive)
            )
        next_state = self._transition(event_type)
        next_sequence = self.sequence + 1
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "recorded_at": datetime.now(timezone.utc),
            "sequence": next_sequence,
            "session_date": self.session_date,
            "session_id": self.session_id,
            "strategy_version": self.strategy_version,
            "payload": dict(payload),
        }
        serialized = json.dumps(
            envelope,
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        output = self.path
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence = next_sequence
        self.state = next_state
        return output

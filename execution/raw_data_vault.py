from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from journal.writer import FORBIDDEN_KEY_FRAGMENTS


@dataclass(frozen=True)
class RawSnapshotReceipt:
    snapshot_id: str
    content_sha256: str
    path: Path


def _forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if any(fragment in str(key).lower() for fragment in FORBIDDEN_KEY_FRAGMENTS):
                return True
            if _forbidden(child):
                return True
    elif isinstance(value, list):
        return any(_forbidden(child) for child in value)
    return False


class RawDataVault:
    """Immutable market-data snapshots with provenance and secret rejection."""

    def __init__(self, root: str | Path = "logs/raw") -> None:
        self.root = Path(root)

    def store(
        self,
        *,
        source: str,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        source_updated_at: datetime,
        received_at: datetime | None = None,
        schema_version: int = 1,
    ) -> RawSnapshotReceipt:
        received = received_at or datetime.now(timezone.utc)
        for name, value in (("source_updated_at", source_updated_at), ("received_at", received)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if _forbidden(request) or _forbidden(response):
            raise ValueError("Sensitive fields are forbidden in the raw market-data vault")
        snapshot_id = str(uuid.uuid4())
        envelope = {
            "schema_version": schema_version,
            "snapshot_id": snapshot_id,
            "source": source,
            "request": request,
            "response": response,
            "source_updated_at": source_updated_at.isoformat(),
            "received_at": received.isoformat(),
        }
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        dated = received.astimezone(timezone.utc).date().isoformat()
        path = self.root / dated / f"{snapshot_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("wb") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        self._append_index(snapshot_id, digest, received)
        return RawSnapshotReceipt(snapshot_id, digest, path)

    def _append_index(self, snapshot_id: str, digest: str, received: datetime) -> None:
        """Append-only hash ledger so later in-place edits are detectable."""

        entry = json.dumps(
            {
                "snapshot_id": snapshot_id,
                "content_sha256": digest,
                "stored_at": received.astimezone(timezone.utc).isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with (self.root / "vault_index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(entry + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _index_digest(snapshot_path: Path, snapshot_id: str) -> str | None:
        """Return the ledger digest for a snapshot, or None if not indexed."""

        index_path = snapshot_path.parent.parent / "vault_index.jsonl"
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        digest: str | None = None
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError("RAW_VAULT_INDEX_CORRUPT")
            if isinstance(entry, dict) and entry.get("snapshot_id") == snapshot_id:
                if digest is not None and digest != entry.get("content_sha256"):
                    raise ValueError("RAW_VAULT_INDEX_CONFLICT")
                digest = str(entry.get("content_sha256") or "") or None
        return digest

    @staticmethod
    def verify(path: str | Path, expected_sha256: str | None = None) -> RawSnapshotReceipt:
        snapshot_path = Path(path)
        try:
            raw_bytes = snapshot_path.read_bytes()
            envelope = json.loads(raw_bytes)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("RAW_SNAPSHOT_INVALID") from error
        required = {
            "schema_version", "snapshot_id", "source", "request", "response",
            "source_updated_at", "received_at",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise ValueError("RAW_SNAPSHOT_ENVELOPE_INVALID")
        canonical = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        digest = hashlib.sha256(canonical).hexdigest()
        if raw_bytes != canonical:
            raise ValueError("RAW_SNAPSHOT_NOT_CANONICAL")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("RAW_SNAPSHOT_HASH_MISMATCH")
        indexed_digest = RawDataVault._index_digest(snapshot_path, str(envelope["snapshot_id"]))
        if indexed_digest is not None and indexed_digest != digest:
            raise ValueError("RAW_SNAPSHOT_INDEX_MISMATCH")
        if _forbidden(envelope["request"]) or _forbidden(envelope["response"]):
            raise ValueError("RAW_SNAPSHOT_CONTAINS_SENSITIVE_FIELDS")
        return RawSnapshotReceipt(str(envelope["snapshot_id"]), digest, snapshot_path)

"""Deterministic local adjudication of the six official market-time checks.

Until now the six checks that gate formal Shadow (see
monitoring.shadow_readiness.MONDAY_MARKET_CHECKS) were reported by the market-
hours agent itself. This module lets deterministic local code independently
decide each check from the immutable raw snapshot, so `monday_go` no longer
rests on the agent's self-report.

Design honesty: the raw collector is market-data-only (it deliberately excludes
every account/order/position/session tool so no identifier can enter the
vault). Checks that need those domains therefore return UNKNOWN with a precise
reason unless a separately-obtained, already-reconciled result is supplied.
UNKNOWN and FAIL both fail closed: neither can satisfy a check.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from execution.official_mcp_collector import (
    RAW_REQUIRED_TOOLS,
    _ISO_TIMESTAMP,
    _parse_iso_aware,
)
from execution.raw_data_vault import RawDataVault
from monitoring.bar_time_checks import within_regular_session
from monitoring.shadow_readiness import MONDAY_MARKET_CHECKS


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MarketCheckResult:
    name: str
    status: CheckStatus
    evidence: tuple[str, ...]
    reason: str | None

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


def _load_envelope(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tool_results(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    response = envelope.get("response")
    if not isinstance(response, dict):
        return []
    results = response.get("tool_results")
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _market_projection_digest(envelope: Mapping[str, Any]) -> str:
    """Deterministic digest of the harvested market data.

    Canonicalizes the tool_results into sorted-key JSON and hashes it. Two
    independent parses of the same immutable snapshot must yield the same
    digest, which is what 'identical raw snapshot -> identical features without
    an LLM' means at the transport-to-normalized-structure boundary.
    """

    projection = [
        {"tool": r.get("tool"), "output": r.get("output")}
        for r in _tool_results(envelope)
    ]
    canonical = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _check_raw_snapshot(path: Path) -> MarketCheckResult:
    name = "official_raw_mcp_snapshot"
    try:
        receipt = RawDataVault.verify(path, require_indexed=True)
    except ValueError as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"VAULT_VERIFY_FAILED:{error}")
    try:
        envelope = _load_envelope(path)
    except (OSError, json.JSONDecodeError) as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"SNAPSHOT_UNREADABLE:{error}")
    if envelope.get("source") != "ROBINHOOD_OFFICIAL_MCP":
        return MarketCheckResult(name, CheckStatus.FAIL, (), "SOURCE_NOT_OFFICIAL_MCP")
    called = {r.get("tool") for r in _tool_results(envelope)}
    missing = RAW_REQUIRED_TOOLS - called
    if missing:
        return MarketCheckResult(
            name, CheckStatus.FAIL, (), "MISSING_TOOLS:" + ",".join(sorted(missing))
        )
    return MarketCheckResult(
        name,
        CheckStatus.PASS,
        (f"snapshot_id={receipt.snapshot_id}", f"sha256={receipt.content_sha256}"),
        None,
    )


def _check_reproducibility(path: Path, raw_ok: bool) -> MarketCheckResult:
    name = "raw_to_feature_reproducibility"
    if not raw_ok:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "DEPENDS_ON_RAW_SNAPSHOT")
    try:
        first = _market_projection_digest(_load_envelope(path))
        second = _market_projection_digest(_load_envelope(path))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"PROJECTION_FAILED:{error}")
    if first != second:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "NON_DETERMINISTIC_PROJECTION")
    return MarketCheckResult(name, CheckStatus.PASS, (f"projection_sha256={first}",), None)


def _received_at(envelope: Mapping[str, Any]) -> datetime | None:
    value = envelope.get("received_at")
    if not isinstance(value, str):
        return None
    return _parse_iso_aware(value)


def _check_fresh_option_quote(path: Path, raw_ok: bool, max_age_seconds: int) -> MarketCheckResult:
    name = "fresh_option_quote"
    if not raw_ok:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "DEPENDS_ON_RAW_SNAPSHOT")
    try:
        envelope = _load_envelope(path)
    except (OSError, json.JSONDecodeError) as error:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), f"SNAPSHOT_UNREADABLE:{error}")
    received = _received_at(envelope)
    if received is None:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "NO_TRUSTED_RECEIPT_TIME")
    quote_results = [
        r for r in _tool_results(envelope) if r.get("tool") == "get_option_quotes"
    ]
    if not quote_results:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "NO_OPTION_QUOTE_RESULT")
    freshest: datetime | None = None
    for quote_result in quote_results:
        text = json.dumps(quote_result.get("output"), ensure_ascii=False)
        for match in _ISO_TIMESTAMP.findall(text):
            parsed = _parse_iso_aware(match)
            if parsed is None or parsed > received:
                continue
            if freshest is None or parsed > freshest:
                freshest = parsed
    if freshest is None:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "NO_QUOTE_TIMESTAMP")
    age = (received - freshest).total_seconds()
    if age > max_age_seconds:
        return MarketCheckResult(
            name, CheckStatus.FAIL, (), f"QUOTE_STALE:{age:.1f}s>{max_age_seconds}s"
        )
    return MarketCheckResult(
        name, CheckStatus.PASS, (f"quote_age_seconds={age:.3f}", f"quote_updated_at={freshest.isoformat()}"), None
    )


def _check_session_from_quotes(
    path: Path,
    raw_ok: bool,
    snapshot_symbol: str | None,
    *,
    maximum_last_trade_age_seconds: int = 900,
) -> MarketCheckResult:
    """Adjudicate the instrument session from the snapshot's OWN equity quote.

    This replaces an earlier design that tried to harvest ``get_equity_tradability``
    into a separate probe. That tool requires ``account_number`` (it reports
    per-account eligibility), so a tradability probe would either be unable to
    call it or would have to carry an account identifier into the immutable
    vault — breaking the invariant that no account identifier ever enters it.

    ``get_equity_quotes`` is already in the raw collector's allowlist, needs no
    account context, and is already inside every snapshot. Its ``state``,
    ``has_traded`` and ``venue_last_trade_time`` fields answer the session
    question directly, and because the evidence IS the main snapshot it inherits
    that snapshot's integrity and replay protection for free.

    The last-trade-age bound is only enforced inside the regular session: a
    pre-open canary legitimately carries the prior session's last trade.
    """

    name = "official_instrument_session"
    if not raw_ok:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "DEPENDS_ON_RAW_SNAPSHOT")
    try:
        envelope = _load_envelope(path)
    except (OSError, json.JSONDecodeError) as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"SNAPSHOT_UNREADABLE:{error}")
    received = _received_at(envelope)
    if received is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "NO_TRUSTED_RECEIPT_TIME")

    rows: list[Mapping[str, Any]] = []
    for result in _tool_results(envelope):
        if result.get("tool") != "get_equity_quotes":
            continue
        output = result.get("output")
        data = output.get("data", output) if isinstance(output, Mapping) else output
        candidates = data.get("results") if isinstance(data, Mapping) else data
        if isinstance(candidates, list):
            rows.extend(row for row in candidates if isinstance(row, Mapping))
    if not rows:
        return MarketCheckResult(name, CheckStatus.UNKNOWN, (), "NO_EQUITY_QUOTE_RESULT")

    target = None
    for row in rows:
        quote = row.get("quote") if isinstance(row.get("quote"), Mapping) else row
        symbol = str(quote.get("symbol") or "")
        if snapshot_symbol and symbol.upper() == snapshot_symbol.upper():
            target = quote
            break
    if target is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "NO_QUOTE_FOR_SNAPSHOT_SYMBOL")

    state = str(target.get("state") or "")
    if state != "active":
        return MarketCheckResult(
            name, CheckStatus.FAIL, (), f"INSTRUMENT_STATE_NOT_ACTIVE:{state or 'UNKNOWN'}",
        )
    if target.get("has_traded") is not True:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "INSTRUMENT_HAS_NOT_TRADED")

    last_trade = _parse_iso_aware(str(target.get("venue_last_trade_time") or ""))
    if last_trade is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "NO_VENUE_LAST_TRADE_TIME")
    if last_trade > received:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "VENUE_LAST_TRADE_IN_FUTURE")
    age = (received - last_trade).total_seconds()
    in_session = within_regular_session(received)
    if in_session and age > maximum_last_trade_age_seconds:
        return MarketCheckResult(
            name, CheckStatus.FAIL, (),
            f"LAST_TRADE_STALE_IN_SESSION:{age:.0f}s>{maximum_last_trade_age_seconds}s",
        )
    return MarketCheckResult(
        name,
        CheckStatus.PASS,
        (
            f"symbol={target.get('symbol')}",
            "state=active",
            "has_traded=true",
            f"venue_last_trade_time={last_trade.isoformat()}",
            f"last_trade_age_seconds={age:.3f}",
            f"regular_session={in_session}",
        ),
        None,
    )


def _session_result(
    session: Mapping[str, Any] | None,
    snapshot_symbol: str | None = None,
) -> MarketCheckResult:
    """Instrument-session check: UNKNOWN unless live tradability evidence is fed.

    The market-data raw snapshot deliberately excludes session/tradability
    tools, so this check can only PASS when the caller supplies evidence from a
    live ``get_equity_tradability`` read:
    {"active": true, "tool": "get_equity_tradability", "symbol": "SPY",
     "evidence": ["..."]}. Anything malformed fails closed.
    """

    name = "official_instrument_session"
    if session is None:
        return MarketCheckResult(
            name, CheckStatus.UNKNOWN, (),
            "NO_OFFICIAL_SESSION_TOOL_IN_MARKET_DATA_SNAPSHOT",
        )
    if not isinstance(session, Mapping):
        return MarketCheckResult(name, CheckStatus.FAIL, (), "MALFORMED_EVIDENCE_SHAPE")
    if snapshot_symbol and str(session.get("symbol") or "").upper() != snapshot_symbol.upper():
        return MarketCheckResult(name, CheckStatus.FAIL, (), "SESSION_SYMBOL_MISMATCH")
    if session.get("tool") != "get_equity_tradability":
        return MarketCheckResult(name, CheckStatus.FAIL, (), "SESSION_EVIDENCE_WRONG_TOOL")
    if session.get("active") is not True:
        reason = str(session.get("reason") or "INSTRUMENT_NOT_ACTIVE")
        return MarketCheckResult(name, CheckStatus.FAIL, (), reason)
    evidence = session.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        return MarketCheckResult(name, CheckStatus.FAIL, (), "SESSION_EVIDENCE_MISSING")
    return MarketCheckResult(name, CheckStatus.PASS, tuple(evidence), None)


def _check_fresh_quote_probe(
    path: Path,
    max_age_seconds: int,
    *,
    adjudicated_at: datetime,
    snapshot_received_at: datetime | None,
    contemporaneity_seconds: int,
) -> MarketCheckResult:
    """Adjudicate quote freshness against a dedicated fresh-quote probe snapshot.

    The full six-tool collection takes minutes, so by envelope receipt time the
    option quotes gathered mid-run are structurally 60s+ old — a measurement
    artifact, not market staleness. The probe is a separate vault-stored
    snapshot containing ONLY one get_option_quotes call, collected in seconds,
    so quote-updated-at vs received_at genuinely measures freshness. The probe
    fails closed like everything else: an invalid/tampered probe is a FAIL,
    never a fallback to the stale measurement.
    """

    name = "fresh_option_quote"
    try:
        receipt = RawDataVault.verify(path, require_indexed=True)
    except (OSError, ValueError) as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"PROBE_VERIFY_FAILED:{error}")
    try:
        envelope = _load_envelope(path)
    except (OSError, json.JSONDecodeError) as error:
        return MarketCheckResult(name, CheckStatus.FAIL, (), f"PROBE_UNREADABLE:{error}")
    if envelope.get("source") != "ROBINHOOD_OFFICIAL_MCP":
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_SOURCE_NOT_OFFICIAL_MCP")
    request = envelope.get("request")
    if not isinstance(request, Mapping) or request.get("probe") != "FRESH_OPTION_QUOTE":
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_MARKER_MISSING")
    received = _received_at(envelope)
    if received is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_NO_TRUSTED_RECEIPT_TIME")
    # Replay firewall: the probe must belong to THIS adjudication, not be a
    # historical envelope whose internal timestamps are self-consistent.
    probe_lag = (adjudicated_at - received).total_seconds()
    if probe_lag < -60:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_RECEIPT_IN_FUTURE")
    if probe_lag > contemporaneity_seconds:
        return MarketCheckResult(
            name, CheckStatus.FAIL, (),
            f"PROBE_NOT_CONTEMPORANEOUS:{probe_lag:.0f}s>{contemporaneity_seconds}s",
        )
    if snapshot_received_at is not None and received < snapshot_received_at:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_PREDATES_SNAPSHOT")
    quote_result = next(
        (r for r in _tool_results(envelope) if r.get("tool") == "get_option_quotes"), None
    )
    if quote_result is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_HAS_NO_OPTION_QUOTE_RESULT")
    text = json.dumps(quote_result.get("output"), ensure_ascii=False)
    freshest: datetime | None = None
    for match in _ISO_TIMESTAMP.findall(text):
        parsed = _parse_iso_aware(match)
        if parsed is None or parsed > received:
            continue
        if freshest is None or parsed > freshest:
            freshest = parsed
    if freshest is None:
        return MarketCheckResult(name, CheckStatus.FAIL, (), "PROBE_NO_QUOTE_TIMESTAMP")
    age = (received - freshest).total_seconds()
    if age > max_age_seconds:
        return MarketCheckResult(
            name, CheckStatus.FAIL, (), f"QUOTE_STALE:{age:.1f}s>{max_age_seconds}s"
        )
    return MarketCheckResult(
        name,
        CheckStatus.PASS,
        (
            f"quote_age_seconds={age:.3f}",
            f"quote_updated_at={freshest.isoformat()}",
            f"probe_snapshot_id={receipt.snapshot_id}",
        ),
        None,
    )


def _domain_result(name: str, reconciliation: Mapping[str, Any] | None) -> MarketCheckResult:
    """Account/orders-positions checks: UNKNOWN unless a reconciled result is fed.

    The market-data raw snapshot never contains these domains (by design), so
    they can only PASS when a caller supplies an already-verified reconciliation
    object of the form {"reconciled": true, "evidence": ["...", ...]}.
    """

    if reconciliation is None:
        return MarketCheckResult(
            name, CheckStatus.UNKNOWN, (),
            "REQUIRES_ACCOUNT_DOMAIN_READ_ABSENT_FROM_MARKET_DATA_SNAPSHOT",
        )
    if not isinstance(reconciliation, Mapping):
        return MarketCheckResult(name, CheckStatus.FAIL, (), "MALFORMED_EVIDENCE_SHAPE")
    if reconciliation.get("reconciled") is not True:
        reason = str(reconciliation.get("reason") or "NOT_RECONCILED")
        return MarketCheckResult(name, CheckStatus.FAIL, (), reason)
    evidence = reconciliation.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        return MarketCheckResult(name, CheckStatus.FAIL, (), "RECONCILIATION_EVIDENCE_MISSING")
    return MarketCheckResult(name, CheckStatus.PASS, tuple(evidence), None)


def _harvested_or_supplied_session(
    path: Path,
    raw_ok: bool,
    snapshot_symbol: str | None,
    instrument_session: Mapping[str, Any] | None,
) -> MarketCheckResult:
    harvested = _check_session_from_quotes(path, raw_ok, snapshot_symbol)
    # Only fall back to a typed claim when the snapshot simply has no quote to
    # read. A harvested FAIL is a real finding and must never be talked over.
    if harvested.reason in ("NO_EQUITY_QUOTE_RESULT", "DEPENDS_ON_RAW_SNAPSHOT"):
        supplied = _session_result(instrument_session, snapshot_symbol)
        if supplied.status is not CheckStatus.UNKNOWN:
            return supplied
    return harvested


def verify_market_checks(
    snapshot_path: str | Path,
    *,
    maximum_option_quote_age_seconds: int = 10,
    maximum_probe_quote_age_seconds: int = 15,
    probe_contemporaneity_seconds: int = 300,
    account_reconciliation: Mapping[str, Any] | None = None,
    orders_positions_reconciliation: Mapping[str, Any] | None = None,
    instrument_session: Mapping[str, Any] | None = None,
    fresh_quote_snapshot: str | Path | None = None,
    adjudicated_at: datetime | None = None,
) -> dict[str, MarketCheckResult]:
    """Adjudicate all six market checks deterministically from a raw snapshot.

    ``instrument_session`` and ``fresh_quote_snapshot`` follow the same pattern
    as the account-domain reconciliations: separately-obtained live evidence
    supplied by the caller, validated fail-closed here. Without them those two
    checks stay UNKNOWN/stale exactly as before.

    The probe path enforces a replay firewall: probe marker present, receipt
    contemporaneous with THIS adjudication (``probe_contemporaneity_seconds``),
    and receipt not earlier than the main snapshot's. ``adjudicated_at`` exists
    for deterministic tests; production callers leave it None (= now).

    ``maximum_probe_quote_age_seconds`` is deliberately slightly wider than the
    10s in-snapshot policy: the probe envelope's received_at is stamped after
    the CLI's terminal turn and teardown (~3-5s measured), which is transport
    overhead, not market staleness.
    """

    now = adjudicated_at if adjudicated_at is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("adjudicated_at must be timezone-aware")
    path = Path(snapshot_path)
    raw = _check_raw_snapshot(path)
    raw_ok = raw.status is CheckStatus.PASS
    snapshot_received: datetime | None = None
    snapshot_symbol: str | None = None
    if raw_ok:
        try:
            envelope = _load_envelope(path)
            snapshot_received = _received_at(envelope)
            request = envelope.get("request")
            if isinstance(request, Mapping):
                symbol = request.get("symbol")
                snapshot_symbol = str(symbol) if isinstance(symbol, str) else None
        except (OSError, json.JSONDecodeError):
            pass
    if fresh_quote_snapshot is not None:
        fresh = _check_fresh_quote_probe(
            Path(fresh_quote_snapshot),
            maximum_probe_quote_age_seconds,
            adjudicated_at=now,
            snapshot_received_at=snapshot_received,
            contemporaneity_seconds=probe_contemporaneity_seconds,
        )
    else:
        fresh = _check_fresh_option_quote(path, raw_ok, maximum_option_quote_age_seconds)
    results = {
        "official_raw_mcp_snapshot": raw,
        "raw_to_feature_reproducibility": _check_reproducibility(path, raw_ok),
        # Harvested first: the snapshot's own quote is model-independent, so a
        # typed claim can only ever be a fallback, never an upgrade.
        "official_instrument_session": _harvested_or_supplied_session(
            path, raw_ok, snapshot_symbol, instrument_session,
        ),
        "official_account_cash_reconciliation": _domain_result(
            "official_account_cash_reconciliation", account_reconciliation
        ),
        "official_orders_positions_reconciliation": _domain_result(
            "official_orders_positions_reconciliation", orders_positions_reconciliation
        ),
        "fresh_option_quote": fresh,
    }
    # Guard against drift from the canonical check set.
    assert set(results) == set(MONDAY_MARKET_CHECKS)
    return results


def to_evidence_document(results: Mapping[str, MarketCheckResult]) -> dict[str, Any]:
    """Render results in the schema that shadow-readiness --market-checks loads."""

    provenance = {
        "official_raw_mcp_snapshot": "HARVESTED_VAULT_SNAPSHOT",
        "raw_to_feature_reproducibility": "HARVESTED_VAULT_SNAPSHOT",
        "fresh_option_quote": "HARVESTED_VAULT_SNAPSHOT_OR_PROBE",
        "official_instrument_session": "HARVESTED_VAULT_SNAPSHOT",
        "official_account_cash_reconciliation": "SUPPLIED_EVIDENCE_SELF_REPORTED",
        "official_orders_positions_reconciliation": "SUPPLIED_EVIDENCE_SELF_REPORTED",
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            name: {**result.to_dict(), "provenance": provenance.get(name, "UNKNOWN")}
            for name, result in results.items()
        },
        "note": "Deterministic local adjudication. UNKNOWN/FAIL both fail closed; "
        "a check counts as satisfied only when status is PASS with evidence. "
        "SUPPLIED_EVIDENCE checks rest on the gate agent's live reads and are "
        "the weakest tier; harvested/probe checks are model-independent.",
    }

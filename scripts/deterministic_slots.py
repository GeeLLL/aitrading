"""Deterministic slot pipelines: PILOT_SAMPLE and CLOSE_SUMMARY without an LLM.

Until 2026-08-01 every non-canary slot spawned a Claude CLI agent that followed
a prose prompt — it decided which CLI commands to run, hand-wrote trajectory
JSON, and on at least two days wrote brand-new ~400-line close-summary scripts
from scratch. The day's primary data products therefore depended on a model
improvising in production. These pipelines replace that with fixed Python:

  PILOT_SAMPLE  = bars probe -> frozen evaluation -> decision record
                  -> refresh open trajectories -> (maybe) open one candidate
                  -> terminal summary
  CLOSE_SUMMARY = deterministic EOD report -> safety/incident/label enrichment
                  -> terminal summary

The ONLY remaining model involvement is inside the collectors themselves (the
Claude CLI as MCP transport, until the direct OAuth client is authorized on the
Mac). No prompt decides anything here; identical inputs produce identical
outputs. MARKET_GATE still uses the agent path (its account-domain
reconciliation needs MCP account tools the transport-only collectors
deliberately exclude).

Both runners write ``<run_id>.summary.json`` — the terminal receipt the worker
requires for COMPLETED status and the dashboard's data source.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.official_mcp_collector import (
    OfficialCollectorError,
    collect_fresh_option_quote_probe,
    collect_official_raw_snapshot,
    collect_universe_bars_probes,
)
from monitoring.scheduler_watchdog import unresolved_incident_ids
from research.trajectory_recorder import (
    candidate_event,
    load_day_events,
    nearest_the_money,
    observation_event,
    open_refresh_targets,
    option_instruments,
    option_quotes_by_instrument,
    underlying_last_trade,
    write_event,
)
from research.universe_evaluation import evaluate_snapshot
from strategy.policy_labels import load_policy_labels
from strategy.universe import load_universe_policy

MAX_PROBE_INSTRUMENTS = 6   # fresh-quote-probe CLI bound


def _use_direct_transport() -> bool:
    """Direct (LLM-free) MCP transport is an explicit opt-in: the owner sets
    ROBINHOOD_TRANSPORT=direct only after the Mac-side OAuth login and a passing
    A/B against the CLI path. Never switches silently."""
    import os
    from execution.mcp_oauth import DEFAULT_CACHE_PATH

    return (
        os.environ.get("ROBINHOOD_TRANSPORT") == "direct"
        and (ROOT / DEFAULT_CACHE_PATH).is_file()
    )


def _direct_token_provider():
    from execution.mcp_oauth import DEFAULT_CACHE_PATH, OAuthTokenProvider, TokenCache, discover
    from execution.robinhood_direct_collector import ROBINHOOD_MCP_ENDPOINT

    metadata = discover(ROBINHOOD_MCP_ENDPOINT)
    return OAuthTokenProvider(metadata, TokenCache(ROOT / DEFAULT_CACHE_PATH))


def _collect_bars(symbols: list[str], project_root: Path) -> list:
    """Vault the universe's bars, returning one receipt per probe call.

    The direct transport has no payload cap, so it stays a single call. The
    Claude-CLI transport must chunk: all thirteen symbols in one call made the
    tool error outright, which FAILED_CLOSED every pilot slot on 2026-08-03.
    """
    if _use_direct_transport():
        from execution.robinhood_direct_collector import collect_universe_bars_probe_direct

        return [collect_universe_bars_probe_direct(
            symbols, token_provider=_direct_token_provider(), project_root=project_root,
        )]
    return collect_universe_bars_probes(symbols, project_root=project_root)


def _collect_quote_probe(instrument_ids: list[str], project_root: Path):
    if _use_direct_transport():
        from execution.robinhood_direct_collector import collect_fresh_option_quote_probe_direct

        return collect_fresh_option_quote_probe_direct(
            instrument_ids, token_provider=_direct_token_provider(), project_root=project_root,
        )
    return collect_fresh_option_quote_probe(instrument_ids, project_root=project_root)


def _collect_snapshot(symbol: str, project_root: Path):
    if _use_direct_transport():
        from execution.robinhood_direct_collector import collect_official_raw_snapshot_direct

        return collect_official_raw_snapshot_direct(
            symbol, token_provider=_direct_token_provider(), project_root=project_root,
        )
    return collect_official_raw_snapshot(symbol, project_root=project_root, resilient=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _read_envelope(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OfficialCollectorError("VAULT_ENVELOPE_NOT_OBJECT")
    return payload


def _received_at(envelope: dict[str, Any]) -> datetime:
    raw = str(envelope.get("received_at") or "")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    return datetime.fromisoformat(normalized)


def run_pilot_sample(
    *,
    run_id: str,
    scheduled: datetime,
    now: datetime,
    log_root: Path,
    trajectory_root: Path,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    """One deterministic pilot sample. Returns the terminal summary (also
    written to ``<run_id>.summary.json``). Fail-closed steps record their
    failure and stop; nothing is invented."""
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "kind": "PILOT_SAMPLE",
        "pipeline": "DETERMINISTIC_PYTHON_V1",
        "scheduled_for": scheduled.astimezone(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAILED_CLOSED",
        "steps": [],
        "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
        "read_only": True,
        "mcp_transport": "PYTHON_DIRECT_MCP" if _use_direct_transport() else "CLAUDE_CLI",
    }
    steps: list[dict[str, Any]] = summary["steps"]

    def finish(status: str) -> dict[str, Any]:
        summary["status"] = status
        summary["ended_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(log_root / f"{run_id}.summary.json", summary)
        return summary

    # 1. Bars probe over the frozen universe.
    try:
        symbols = list(load_universe_policy(str(project_root / "config/universe.toml"))["symbols"])
        bars_receipts = _collect_bars(symbols, project_root)
        steps.append({"step": "BARS_PROBE", "ok": True,
                      "snapshot": str(bars_receipts[0].path),
                      "snapshots": [str(r.path) for r in bars_receipts],
                      "sha256": bars_receipts[0].content_sha256,
                      "sha256_all": [r.content_sha256 for r in bars_receipts],
                      "symbols": symbols})
    except (OfficialCollectorError, OSError, ValueError, KeyError) as error:
        steps.append({"step": "BARS_PROBE", "ok": False, "error": f"{type(error).__name__}: {error}"})
        return finish("FAILED_CLOSED")

    # 2. Frozen evaluation -> decision record.
    try:
        decision = evaluate_snapshot([r.path for r in bars_receipts], project_root=project_root)
        registry = load_policy_labels(project_root)
        decision["run_id"] = run_id
        decision["policy_label_registry_version"] = registry.version
        decision_path = _atomic_json(log_root / f"{run_id}.decision.json", decision)
        steps.append({"step": "EVALUATE", "ok": decision.get("status") == "OK",
                      "decision": str(decision_path),
                      "admissible": decision.get("decision_admissible"),
                      "qualified": decision.get("qualified_symbols") or []})
    except (OSError, ValueError, KeyError) as error:
        steps.append({"step": "EVALUATE", "ok": False, "error": f"{type(error).__name__}: {error}"})
        return finish("FAILED_CLOSED")

    # 3. Refresh open trajectories (fill/horizon evidence). A probe failure here
    # degrades (recorded, not fatal): the EOD report adjudicates what exists.
    groups = load_day_events(trajectory_root)
    targets = open_refresh_targets(groups, now)
    refreshed = 0
    if targets:
        instrument_ids = sorted({
            str(target["candidate"].get("instrument_id") or "") for target in targets
        } - {""})[:MAX_PROBE_INSTRUMENTS]
        try:
            probe_receipt = _collect_quote_probe(instrument_ids, project_root)
            probe_envelope = _read_envelope(probe_receipt.path)
            probe_quotes = option_quotes_by_instrument(probe_envelope)
            probe_received = _received_at(probe_envelope)
            probe_source = probe_envelope.get("source_updated_at")
            for target in targets:
                quote = probe_quotes.get(str(target["candidate"].get("instrument_id")))
                if quote is None:
                    continue
                event = observation_event(
                    event_type=target["event_type"],
                    candidate=target["candidate"],
                    quote=quote,
                    quote_received_at=probe_received,
                    source_updated_at=probe_source if isinstance(probe_source, str) else None,
                )
                write_event(trajectory_root, event)
                refreshed += 1
            steps.append({"step": "REFRESH", "ok": True, "targets": len(targets),
                          "refreshed": refreshed, "snapshot": str(probe_receipt.path)})
        except (OfficialCollectorError, OSError, ValueError) as error:
            steps.append({"step": "REFRESH", "ok": False, "targets": len(targets),
                          "error": f"{type(error).__name__}: {error}"})
    else:
        steps.append({"step": "REFRESH", "ok": True, "targets": 0, "refreshed": 0})

    # 4. Open at most one new candidate per day, only when the frozen evaluation
    # admits one and a policy label actually fires. Deterministic; no ranking AI.
    opened: str | None = None
    has_open_candidate = any(
        any(event.get("event_type") == "CANDIDATE" and not event.get("rejection_reasons")
            for event in events)
        for events in groups.values()
    )
    qualified = list(decision.get("qualified_symbols") or [])
    if decision.get("decision_admissible") and qualified and not has_open_candidate:
        target_symbol = str(qualified[0])
        symbol_report = (decision.get("symbols") or {}).get(target_symbol) or {}
        volume_ratio = symbol_report.get("volume_ratio")
        labels = (
            list(registry.labels_for_volume_ratio(float(volume_ratio)))
            if isinstance(volume_ratio, (int, float)) else []
        )
        if labels:
            try:
                option_receipt = _collect_snapshot(target_symbol, project_root)
                envelope = _read_envelope(option_receipt.path)
                underlying_price = underlying_last_trade(envelope, target_symbol)
                selection = (
                    nearest_the_money(
                        option_instruments(envelope),
                        option_quotes_by_instrument(envelope),
                        underlying_price,
                    ) if underlying_price is not None else None
                )
                if selection is None:
                    steps.append({"step": "OPEN_CANDIDATE", "ok": False,
                                  "symbol": target_symbol,
                                  "error": "NO_QUOTED_INSTRUMENT_NEAR_MONEY"})
                else:
                    instrument, quote = selection
                    source = envelope.get("source_updated_at")
                    event = candidate_event(
                        instrument=instrument,
                        quote=quote,
                        decision_time=now,
                        quote_received_at=_received_at(envelope),
                        source_updated_at=source if isinstance(source, str) else None,
                        policy_labels=labels,
                    )
                    write_event(trajectory_root, event)
                    opened = str(event["trajectory_id"])
                    steps.append({"step": "OPEN_CANDIDATE", "ok": True,
                                  "symbol": target_symbol, "labels": labels,
                                  "trajectory_id": opened,
                                  "snapshot": str(option_receipt.path)})
            except (OfficialCollectorError, OSError, ValueError) as error:
                steps.append({"step": "OPEN_CANDIDATE", "ok": False, "symbol": target_symbol,
                              "error": f"{type(error).__name__}: {error}"})
        else:
            steps.append({"step": "OPEN_CANDIDATE", "ok": True, "symbol": target_symbol,
                          "skipped": "NO_POLICY_LABEL_FIRED",
                          "volume_ratio": volume_ratio})
    else:
        steps.append({"step": "OPEN_CANDIDATE", "ok": True,
                      "skipped": ("ALREADY_OPEN_TODAY" if has_open_candidate
                                  else "NOT_ADMISSIBLE_OR_NO_QUALIFIED")})

    # 5. Daily calibration trade (machinery validation, never evidence; frozen
    # rule ported from the pilot prompt — see research/calibration_trade.py).
    from research import calibration_trade as cal

    slot_hhmm = (scheduled.hour, scheduled.minute)
    day = now.astimezone(scheduled.tzinfo).date().isoformat()
    cal_dir = cal.calibration_dir(project_root, day)
    entry = cal.load_entry(project_root, day)
    try:
        if entry is None and cal.entry_allowed(slot_hhmm):
            placed = False
            for cal_symbol in cal.ranked_symbols(decision)[:3]:
                receipt = _collect_snapshot(cal_symbol, project_root)
                envelope = _read_envelope(receipt.path)
                selection = cal.select_calibration_contract(envelope)
                if selection is None:
                    continue
                instrument, quote, band = selection
                source = envelope.get("source_updated_at")
                record = cal.entry_record(
                    run_id=run_id, symbol=cal_symbol, instrument=instrument,
                    quote=quote, premium_band=band,
                    observed_at=_received_at(envelope),
                    source_updated_at=source if isinstance(source, str) else None,
                )
                if cal.write_once(cal_dir / "entry.json", record):
                    steps.append({"step": "CALIBRATION_ENTRY", "ok": True,
                                  "symbol": cal_symbol, "premium_band": band,
                                  "instrument_id": record["instrument_id"]})
                placed = True
                break
            if not placed:
                steps.append({"step": "CALIBRATION_ENTRY", "ok": True,
                              "skipped": "NO_QUALIFYING_CALIBRATION_CONTRACT"})
        elif entry is not None and not (cal_dir / "exit.json").exists():
            reason = cal.exit_due(entry, now, slot_hhmm)
            if reason:
                probe = _collect_quote_probe([str(entry["instrument_id"])], project_root)
                envelope = _read_envelope(probe.path)
                quote = option_quotes_by_instrument(envelope).get(str(entry["instrument_id"]))
                if quote is not None:
                    cal.write_once(cal_dir / "exit.json", cal.exit_record(
                        run_id=run_id, entry=entry, quote=quote,
                        observed_at=_received_at(envelope), exit_reason=reason,
                    ))
                    steps.append({"step": "CALIBRATION_EXIT", "ok": True, "reason": reason})
                else:
                    steps.append({"step": "CALIBRATION_EXIT", "ok": False,
                                  "error": "INSTRUMENT_NOT_IN_PROBE_RESULT"})
            else:
                steps.append({"step": "CALIBRATION_EXIT", "ok": True, "skipped": "NOT_DUE"})
        else:
            steps.append({"step": "CALIBRATION", "ok": True,
                          "skipped": "DONE_OR_ENTRY_WINDOW_CLOSED"})
    except (OfficialCollectorError, OSError, ValueError, KeyError) as error:
        steps.append({"step": "CALIBRATION", "ok": False,
                      "error": f"{type(error).__name__}: {error}"})

    summary["opened_trajectory"] = opened
    summary["refreshed_events"] = refreshed
    return finish("COMPLETED")


def run_close_summary(
    *,
    run_id: str,
    scheduled: datetime,
    now: datetime,
    log_root: Path,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    """Deterministic close: canonical EOD report + safety/incident enrichment.
    Replaces the agent that used to hand-write a fresh close script every day."""
    from main import build_status
    from monitoring.daily_schedule import SESSION_TIMEZONE
    from scripts.eod_report import write_report

    day = now.astimezone(SESSION_TIMEZONE).date()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "kind": "CLOSE_SUMMARY",
        "pipeline": "DETERMINISTIC_PYTHON_V1",
        "scheduled_for": scheduled.astimezone(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
        "read_only": True,
        "constraint_compliance": {
            "mcp_calls_made": 0,
            "market_data_backfilled": False,
            "start_ack_not_rewritten": True,
        },
    }
    warnings: list[str] = []

    try:
        report_path = write_report(day, project_root=project_root)
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except Exception as error:  # report failure must still leave a receipt
        warnings.append(f"EOD_REPORT_FAILED:{type(error).__name__}:{error}")
        report_path, report = None, {}

    summary["eod_report"] = str(report_path) if report_path else None
    summary["slot_coverage"] = report.get("slot_coverage", {})
    summary["pnl"] = report.get("pnl", {})
    summary["policy_label_counts"] = report.get("policy_label_counts", {})
    summary["fill_adjudications"] = report.get("fill_adjudications", {})
    summary["bar_time_audit"] = report.get("bar_time_audit", {})

    # Safety-gate snapshot (the old ad-hoc scripts' most-used enrichment).
    try:
        status = build_status()
        summary["safety"] = {
            key: status.get(key)
            for key in ("system_mode", "live_trading_enabled", "order_tools_enabled",
                        "kill_switch_engaged", "approved_trade_stage")
        }
    except Exception as error:
        warnings.append(f"SAFETY_STATUS_FAILED:{type(error).__name__}")

    # Incident reconciliation: what is still unresolved at the close.
    incident_dir = project_root / "logs/incidents"
    summary["unresolved_incidents"] = list(unresolved_incident_ids(incident_dir))
    summary["incident_files"] = sorted(
        path.name for path in incident_dir.glob("*.scheduler-incident.json")
    ) if incident_dir.is_dir() else []

    # Market-gate detail, when the gate ran today.
    gate_dir = project_root / "logs/qualification" / day.isoformat()
    gate_files = sorted(gate_dir.glob("*.market-checks.json")) if gate_dir.is_dir() else []
    gate_detail = []
    for path in gate_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append(f"UNREADABLE_MARKET_CHECKS:{path.name}")
            continue
        if isinstance(payload, dict):
            gate_detail.append({"file": path.name, "checks": payload.get("checks"),
                                "verdict": payload.get("verdict") or payload.get("status")})
    summary["market_gate"] = gate_detail

    summary["warnings"] = warnings
    summary["status"] = "COMPLETED" if report_path else "FAILED_CLOSED"
    summary["ended_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(log_root / f"{run_id}.summary.json", summary)
    return summary

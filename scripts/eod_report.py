#!/usr/bin/env python3
"""Deterministic end-of-day report: slot coverage + simulated P&L.

Aggregates what the day's launchd slots actually produced — worker summaries,
agent decision files, and quote-trajectory events — into one auditable record:

    logs/eod/<date>.pnl.json     (machine-readable)
    logs/eod/<date>.report.md    (human-readable)

P&L is recomputed HERE, deterministically, from observed trajectory events
using the same friction semantics as strategy/shadow_runner.py (per-contract
fee x2 + regulatory exit fee + exit slippage ticks). LLM-written arithmetic is
never trusted for the headline number.

Read-only over local logs. Never contacts the market, never backfills: a slot
with no data is reported as missing, not repaired. Every figure is stamped
PILOT_EXCLUDED_FROM_PERFORMANCE — this report is operational visibility, not
strategy performance evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import SESSION_TIMEZONE, expected_runs_for_date
from strategy.policy_labels import load_policy_labels

# Loaded from the frozen registry (config/policy_labels.toml) so this report can
# never again drift from the labels the pilot actually records. The registry's
# retired policy labels stay in the classification set, so historical
# trajectories (BASE_25/BASE_30 era) keep counting as policy-of-their-era.
# Before this, POLICY_LABELS was hardcoded to the retired set while the pilot
# emitted BASE_18/BASE_21 — making the daily policy PnL structurally zero.
_LABEL_REGISTRY = load_policy_labels(ROOT)
POLICY_LABELS = set(_LABEL_REGISTRY.policy_for_classification)
SUCCESS_STATUSES = {"COMPLETED"}

# The frozen policy's fill window (strategy_v1.0 maximum_fill_wait_seconds),
# plus a small tolerance for clock-read skew between the limit record and the
# confirming refresh. A quote observed outside this window can NEVER
# adjudicate a fill — this is the deterministic enforcement of the same rule
# the pilot prompt states.
DEFAULT_FILL_WINDOW_SECONDS = 60
FILL_WINDOW_SKEW_SECONDS = 5


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def fill_window_seconds(safety_root: Path) -> int:
    """Read maximum_fill_wait_seconds from the frozen strategy policy."""
    try:
        with (safety_root / "strategy/strategy_v1.0.toml").open("rb") as handle:
            policy = tomllib.load(handle)
    except OSError:
        return DEFAULT_FILL_WINDOW_SECONDS

    def find(obj) -> int | None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "maximum_fill_wait_seconds" and isinstance(value, int):
                    return value
                found = find(value)
                if found is not None:
                    return found
        return None

    return find(policy) or DEFAULT_FILL_WINDOW_SECONDS


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None


def round_trip_friction_usd(safety_config: dict) -> Decimal:
    """Single-contract round-trip friction, identical to shadow_runner semantics."""
    model = safety_config["friction_model"]
    per_contract = Decimal(str(model["per_contract_fee_usd"]))
    regulatory_exit = Decimal(str(model["regulatory_exit_fee_usd"]))
    slippage_ticks = Decimal(str(model["exit_latency_slippage_ticks"]))
    tick_size = Decimal(str(model["option_tick_size_usd"]))
    return per_contract * Decimal("2") + regulatory_exit + slippage_ticks * tick_size * Decimal("100")


def load_trajectories(trajectory_dir: Path, warnings: list[str]) -> dict[str, list[dict]]:
    """Group trajectory event payloads by trajectory_id. Malformed files are
    recorded as warnings, never fatal."""
    groups: dict[str, list[dict]] = {}
    if not trajectory_dir.is_dir():
        return groups
    for path in sorted(trajectory_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append(f"UNREADABLE_TRAJECTORY_FILE:{path.name}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"NON_OBJECT_TRAJECTORY_FILE:{path.name}")
            continue
        trajectory_id = str(payload.get("trajectory_id") or "")
        if not trajectory_id:
            warnings.append(f"MISSING_TRAJECTORY_ID:{path.name}")
            continue
        groups.setdefault(trajectory_id, []).append(payload)
    return groups


def fill_window_dual_clock_audit(groups: dict[str, list[dict]]) -> dict:
    """Per-window elapsed time on BOTH clocks: local receipt vs venue update.

    Adjudication stays on the local receipt clock (authoritative, cannot be
    spoofed by upstream timestamps), but collection latency eats into the 60s
    window on that clock, systematically biasing toward NO_FILL. This audit
    quantifies that bias: a window where the venue clock would have filled but
    the receipt clock did not is flagged, so the bias is measured, not hidden.
    (Ported from the ad-hoc 07-29 close analysis into the canonical report.)
    """
    windows: list[dict] = []
    for trajectory_id, events in sorted(groups.items()):
        candidate = next(
            (event for event in events
             if event.get("event_type") == "CANDIDATE" and not event.get("rejection_reasons")),
            None,
        )
        if candidate is None:
            continue
        limit = _decimal(candidate.get("limit_price")
                         if candidate.get("limit_price") is not None else candidate.get("ask"))
        recorded = _parse_ts(candidate.get("limit_recorded_at")) or _parse_ts(candidate.get("quote_received_at"))
        deadline = _parse_ts(candidate.get("fill_window_deadline"))
        candidate_source = _parse_ts(candidate.get("source_updated_at"))
        if limit is None or recorded is None or deadline is None:
            continue
        window_seconds = (deadline - recorded).total_seconds()
        for quote in (event for event in events if event.get("event_type") == "QUOTE"):
            quote_received = _parse_ts(quote.get("quote_received_at"))
            quote_source = _parse_ts(quote.get("source_updated_at"))
            ask = _decimal(quote.get("ask"))
            if quote_received is None or ask is None:
                continue
            receipt_elapsed = (quote_received - recorded).total_seconds()
            venue_elapsed = (
                (quote_source - candidate_source).total_seconds()
                if quote_source is not None and candidate_source is not None else None
            )
            in_receipt = receipt_elapsed <= window_seconds + FILL_WINDOW_SKEW_SECONDS
            in_venue = (
                venue_elapsed is not None
                and venue_elapsed <= window_seconds + FILL_WINDOW_SKEW_SECONDS
            )
            would_fill = ask <= limit
            windows.append({
                "trajectory_id": trajectory_id,
                "receipt_elapsed_seconds": receipt_elapsed,
                "venue_elapsed_seconds": venue_elapsed,
                "ask_at_or_below_limit": bool(would_fill),
                "in_window_receipt_clock": bool(in_receipt),
                "in_window_venue_clock": bool(in_venue),
                "clock_divergence_changes_outcome": bool(would_fill and in_venue and not in_receipt),
            })
    return {
        "windows": windows,
        "clock_divergence_count": sum(
            1 for window in windows if window["clock_divergence_changes_outcome"]
        ),
        "note": (
            "Adjudication uses the local receipt clock (authoritative). The venue-clock "
            "view quantifies collection-latency bias toward NO_FILL; it never changes "
            "the adjudicated outcome."
        ),
    }


def reconstruct_trade(events: list[dict], friction: Decimal) -> dict:
    """Deterministically replay one trajectory's events into a virtual-trade record.

    Entry rule (mirrors the pilot prompt): a simulated entry exists only if a
    LATER observed QUOTE has ask <= the candidate's recorded ask (the limit).
    Exit uses the HORIZON_CLOSE observed bid. Anything unknowable stays null.
    """
    candidates = [event for event in events if event.get("event_type") == "CANDIDATE"]
    quotes = [event for event in events if event.get("event_type") == "QUOTE"]
    closes = [event for event in events if event.get("event_type") == "HORIZON_CLOSE"]
    candidate = candidates[0] if candidates else None

    record: dict[str, object] = {
        "trajectory_id": events[0].get("trajectory_id"),
        "underlying": events[0].get("underlying"),
        "option_type": events[0].get("option_type"),
        "strike": events[0].get("strike"),
        "expiration_date": events[0].get("expiration_date"),
        "policy_labels": sorted({
            str(label)
            for event in events
            for label in (event.get("policy_labels") or [])
        }),
        "rejected": None,
        "rejection_reasons": [],
        "outcome": "NO_CANDIDATE_EVENT",
        "entry_limit": None,
        "entry_fill": None,
        "exit_bid": None,
        "gross_pnl_usd": None,
        "friction_usd": None,
        "net_pnl_usd": None,
    }
    if candidate is None:
        return record

    reasons = [str(reason) for reason in (candidate.get("rejection_reasons") or [])]
    record["rejected"] = bool(reasons)
    record["rejection_reasons"] = reasons
    if reasons:
        record["outcome"] = "REJECTED_NO_TRADE"
        return record

    limit = _decimal(candidate.get("ask"))
    record["entry_limit"] = None if limit is None else float(limit)
    if limit is None:
        record["outcome"] = "NO_LIMIT_PRICE"
        return record

    candidate_at = _parse_ts(candidate.get("quote_received_at"))
    if candidate_at is None:
        record["outcome"] = "NO_CANDIDATE_TIMESTAMP"
        return record
    window = timedelta(seconds=DEFAULT_FILL_WINDOW_SECONDS + FILL_WINDOW_SKEW_SECONDS)
    fill = None
    window_expired_seen = False
    for quote in sorted(quotes, key=lambda event: str(event.get("quote_received_at") or "")):
        quote_at = _parse_ts(quote.get("quote_received_at"))
        if quote_at is None or quote_at <= candidate_at:
            continue
        if quote_at - candidate_at > window:
            # A quote observed after the frozen fill window proves nothing
            # about fillability inside it — the agent's own NO_FILL stands.
            window_expired_seen = True
            continue
        ask = _decimal(quote.get("ask"))
        if ask is not None and ask <= limit:
            fill = ask
            break
    if fill is None:
        record["outcome"] = "NO_FILL_WINDOW_EXPIRED" if window_expired_seen else "NO_FILL"
        return record
    record["entry_fill"] = float(fill)

    exit_bid = _decimal(closes[0].get("bid")) if closes else None
    if exit_bid is None:
        record["outcome"] = "FILLED_NO_HORIZON_CLOSE"
        return record
    record["exit_bid"] = float(exit_bid)

    gross = (exit_bid - fill) * Decimal("100")
    net = gross - friction
    record["outcome"] = "FILLED_AND_EXITED"
    record["gross_pnl_usd"] = float(gross)
    record["friction_usd"] = float(friction)
    record["net_pnl_usd"] = float(net)
    return record


def entry_cost_hurdle(entry: dict, exit_record: dict, friction_model: dict) -> dict | None:
    """The EX-ANTE cost hurdle this contract faced when it was entered.

    IMPORTANT — why this is not subtracted from the realised P&L. The recorded
    gross is ``(exit_bid - entry_ask) * 100``: bought at the ask, sold at the
    bid. That number ALREADY embeds the full bid-ask cost of both legs, and the
    mid-to-mid move it contains has already absorbed whatever time decay
    occurred while the position was open. Subtracting a modelled spread and
    decay on top of it double-counts both — which an earlier version of this
    report did, reporting an over-stated loss.

    So this block answers a different, forward-looking question: at entry, how
    far did the option have to move to break even? That is the number worth
    knowing before committing, and it is what the cost model is for. Returns
    None when an input is unknown — never estimates.
    """
    from datetime import date as _date

    from research.cost_model import round_trip_cost, understatement_ratio

    bid = _decimal(entry.get("entry_bid"))
    ask = _decimal(entry.get("entry_ask"))
    expiration = entry.get("expiration_date")
    entered = _parse_ts(entry.get("entry_observed_at"))
    exited = _parse_ts(exit_record.get("exit_observed_at"))
    if bid is None or ask is None or not isinstance(expiration, str) or entered is None:
        return None
    try:
        expiry = _date.fromisoformat(expiration)
    except ValueError:
        return None
    dte = (expiry - entered.date()).days
    if dte <= 0:
        return None
    holding_days = Decimal("0") if exited is None else Decimal(
        str(max(0.0, (exited - entered).total_seconds() / 86400.0))
    )
    try:
        cost = round_trip_cost(
            bid=bid, ask=ask, dte_days=dte, holding_days=holding_days,
            friction_model=friction_model,
        )
    except (ValueError, ArithmeticError):
        return None
    ratio = understatement_ratio(cost, friction_model)
    return {
        **cost.to_dict(),
        "basis": "EX_ANTE_HURDLE_AT_ENTRY",
        "dte_days": dte,
        "holding_days": float(round(holding_days, 4)),
        "frozen_friction_usd": float(Decimal(str(friction_model["per_contract_fee_usd"])) * 2
                                     + Decimal(str(friction_model["regulatory_exit_fee_usd"]))
                                     + Decimal(str(friction_model["exit_latency_slippage_ticks"]))
                                     * Decimal(str(friction_model["option_tick_size_usd"])) * Decimal("100")),
        "frozen_understated_by_x": None if ratio is None else float(round(ratio, 3)),
        "note": "Ex-ante hurdle: spread + fees + ATM decay (1/(2*DTE) per "
                "calendar day) the position had to overcome. NOT subtracted "
                "from the realised gross, which is measured ask-to-bid and so "
                "already contains the spread and the decay. The frozen "
                "friction_model omits both, which is why it looks so much "
                "smaller than the real hurdle.",
    }


def calibration_result(
    project_root: Path, day: str, friction: Decimal, friction_model: dict | None = None,
) -> dict:
    """Deterministic P&L for the daily calibration trade (machinery validation).

    The pilot agents write only observed quotes (entry at ask, exit at bid);
    the arithmetic happens HERE. Calibration is excluded from all strategy
    evidence by evidence class and never touches the policy-trade budget.
    """
    directory = project_root / "logs/calibration" / day
    result: dict[str, object] = {
        "status": "NO_ENTRY",
        "evidence_class": "CALIBRATION_EXCLUDED_FROM_PERFORMANCE",
        "entry": None,
        "exit": None,
        "gross_pnl_usd": None,
        "friction_usd": None,
        "net_pnl_usd": None,
    }

    def read(name: str) -> dict | None:
        path = directory / name
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"_unreadable": True}
        return payload if isinstance(payload, dict) else {"_unreadable": True}

    entry = read("entry.json")
    if entry is None:
        return result
    if entry.get("_unreadable"):
        result["status"] = "ENTRY_UNREADABLE"
        return result
    result["entry"] = {
        key: entry.get(key)
        for key in (
            "run_id", "symbol", "instrument_id", "strike", "expiration_date",
            "option_type", "delta", "premium_band", "entry_observed_at",
            "entry_bid", "entry_ask", "entry_mark",
        )
    }
    exit_record = read("exit.json")
    if exit_record is None:
        result["status"] = "OPEN_NOT_CLOSED"
        return result
    if exit_record.get("_unreadable"):
        result["status"] = "EXIT_UNREADABLE"
        return result
    result["exit"] = {
        key: exit_record.get(key)
        for key in ("run_id", "exit_observed_at", "exit_bid", "exit_ask",
                    "exit_mark", "holding_minutes", "exit_reason")
    }
    entry_ask = _decimal(entry.get("entry_ask"))
    exit_bid = _decimal(exit_record.get("exit_bid"))
    if entry_ask is None or exit_bid is None:
        result["status"] = "INCOMPLETE_QUOTES"
        return result
    gross = (exit_bid - entry_ask) * Decimal("100")
    result["status"] = "COMPLETED"
    result["gross_pnl_usd"] = float(gross)
    result["friction_usd"] = float(friction)
    result["net_pnl_usd"] = float(gross - friction)
    if friction_model:
        hurdle = entry_cost_hurdle(entry, exit_record, friction_model)
        if hurdle is not None:
            result["entry_cost_hurdle"] = hurdle
        # The realised gross is ask-to-bid, so spread and decay are already in
        # it. The only cost still to deduct is the fixed fees. The frozen
        # friction number is kept above as the governed figure, but note it
        # also books a slippage tick that the observed bid already reflects.
        fees = (
            Decimal(str(friction_model["per_contract_fee_usd"])) * Decimal("2")
            + Decimal(str(friction_model["regulatory_exit_fee_usd"]))
        )
        result["fees_usd"] = float(fees)
        result["net_pnl_usd_fees_only"] = float(gross - fees)
        result["accounting_note"] = (
            "gross is ask-to-bid and already contains the bid-ask cost and the "
            "time decay incurred while held; net_pnl_usd_fees_only deducts only "
            "the fixed fees. net_pnl_usd (frozen) additionally books a slippage "
            "tick the observed bid already reflects."
        )
    return result


def bar_time_audit(project_root: Path, day: str) -> dict:
    """Deterministically re-verify bar times in every snapshot vaulted today.

    Model-independent: reads the immutable envelopes, not the agents' summaries.
    """
    from monitoring.bar_time_checks import verify_snapshot_bar_times

    directory = project_root / "logs/raw" / day
    snapshots: list[dict] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            report = verify_snapshot_bar_times(path)
            if report.get("reason") == "NO_HISTORICAL_BARS_IN_SNAPSHOT":
                continue  # probe snapshots carry no bars by design
            snapshots.append({
                "snapshot_id": report.get("snapshot_id"),
                "status": report.get("status"),
                "reason": report.get("reason"),
                "freshness_enforced": report.get("freshness_enforced"),
                "irregularities": sorted({
                    item
                    for symbol in report.get("symbols", [])
                    for item in symbol.get("irregularities", [])
                }),
            })
    unsound = [item for item in snapshots if item["status"] != "PASS"]
    return {
        "provenance": "HARVESTED_VAULT_SNAPSHOT",
        "snapshots_checked": len(snapshots),
        "unsound": len(unsound),
        "detail": snapshots,
    }


def build_report(report_date: date, *, project_root: Path = ROOT) -> dict:
    warnings: list[str] = []
    day = report_date.isoformat()

    with (project_root / "config/safety.toml").open("rb") as handle:
        safety_config = tomllib.load(handle)
    friction = round_trip_friction_usd(safety_config)

    # --- Slot coverage -----------------------------------------------------
    worker_dir = project_root / "logs/launchd_worker" / day
    ack_dir = project_root / "logs/scheduler"
    slots = []
    completed = failed = missed = 0
    for run_id, scheduled_for in expected_runs_for_date(report_date):
        ack_exists = (ack_dir / f"{run_id}.start.json").is_file()
        summary_path = worker_dir / f"{run_id}.json"
        status = None
        if summary_path.is_file():
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                status = str(payload.get("status") or "UNKNOWN")
            except (OSError, json.JSONDecodeError):
                status = "UNREADABLE_SUMMARY"
        if status in SUCCESS_STATUSES:
            completed += 1
        elif status is None and not ack_exists:
            missed += 1
        else:
            failed += 1
        slots.append({
            "run_id": run_id,
            "scheduled_for": scheduled_for.isoformat(),
            "ack": ack_exists,
            "status": status,
        })

    # --- Agent decisions (best effort, informational) -----------------------
    decisions = []
    if worker_dir.is_dir():
        for path in sorted(worker_dir.glob("*.decision.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append(f"UNREADABLE_DECISION_FILE:{path.name}")
                continue
            if isinstance(payload, dict):
                decisions.append({"file": path.name, **{
                    key: payload.get(key)
                    for key in ("run_id", "decision", "symbol", "reason", "action")
                    if key in payload
                }})

    # --- Virtual trades from trajectories (deterministic P&L) ---------------
    trajectory_dir = project_root / "logs/quote_trajectories" / day
    groups = load_trajectories(trajectory_dir, warnings)
    trades = [reconstruct_trade(events, friction) for events in groups.values()]
    trades.sort(key=lambda trade: str(trade.get("trajectory_id")))

    def is_policy(trade: dict) -> bool:
        return bool(POLICY_LABELS.intersection(trade.get("policy_labels") or []))

    policy_trades = [trade for trade in trades if is_policy(trade)]
    research_trades = [trade for trade in trades if not is_policy(trade)]

    # Per-label counts and fill-adjudication histogram: makes label drift and a
    # structurally-empty policy bucket immediately visible in the daily report.
    label_counts: dict[str, int] = {}
    adjudication_counts: dict[str, int] = {}
    for trade in trades:
        for label in trade.get("policy_labels") or []:
            label_counts[str(label)] = label_counts.get(str(label), 0) + 1
        outcome = str(trade.get("outcome") or "UNKNOWN")
        adjudication_counts[outcome] = adjudication_counts.get(outcome, 0) + 1
    unknown_labels = sorted(
        label for label in label_counts
        if label not in POLICY_LABELS
        and label not in _LABEL_REGISTRY.research
    )
    if unknown_labels:
        warnings.append(f"UNREGISTERED_POLICY_LABELS:{','.join(unknown_labels)}")

    def bucket_totals(bucket: list[dict]) -> dict:
        realized = [trade for trade in bucket if trade["net_pnl_usd"] is not None]
        return {
            "trajectories": len(bucket),
            "filled_and_exited": len(realized),
            "gross_pnl_usd": float(sum(Decimal(str(trade["gross_pnl_usd"])) for trade in realized)) if realized else 0.0,
            "net_pnl_usd": float(sum(Decimal(str(trade["net_pnl_usd"])) for trade in realized)) if realized else 0.0,
        }

    return {
        "schema_version": 1,
        "date": day,
        "generated_at": datetime.now(SESSION_TIMEZONE).isoformat(),
        "slot_coverage": {
            "expected": len(slots),
            "completed": completed,
            "failed": failed,
            "missed": missed,
            "slots": slots,
        },
        "decisions": decisions,
        "virtual_trades": {
            "policy": policy_trades,
            "research_counterfactual": research_trades,
        },
        "policy_label_registry_version": _LABEL_REGISTRY.version,
        "policy_label_counts": label_counts,
        "fill_adjudications": adjudication_counts,
        "fill_window_audit": fill_window_dual_clock_audit(groups),
        "pnl": {
            "friction_model": "CONSERVATIVE_UNCALIBRATED",
            "round_trip_friction_usd": float(friction),
            "policy": bucket_totals(policy_trades),
            "research_counterfactual": bucket_totals(research_trades),
        },
        "calibration_trade": calibration_result(
            project_root, day, friction, safety_config.get("friction_model"),
        ),
        "bar_time_audit": bar_time_audit(project_root, day),
        "warnings": warnings,
        "evidence_class": "PILOT_EXCLUDED_FROM_PERFORMANCE",
        "caveats": [
            "Simulated P&L from observed read-only quotes; no orders were placed.",
            "Pilot data is excluded from formal strategy performance by design.",
            "Missing slots are reported, never backfilled.",
        ],
    }


def render_markdown(report: dict) -> str:
    coverage = report["slot_coverage"]
    pnl = report["pnl"]
    lines = [
        f"# End-of-day report — {report['date']}",
        "",
        f"*Generated {report['generated_at']} — {report['evidence_class']}*",
        "",
        "## Slot coverage",
        "",
        f"- expected: **{coverage['expected']}**",
        f"- completed: **{coverage['completed']}**",
        f"- failed: **{coverage['failed']}**",
        f"- missed: **{coverage['missed']}**",
        "",
        "## Simulated P&L (deterministic, friction-adjusted)",
        "",
        f"- policy trades net: **${pnl['policy']['net_pnl_usd']:.2f}** "
        f"({pnl['policy']['filled_and_exited']} filled/exited of {pnl['policy']['trajectories']} trajectories)",
        f"- research counterfactual net: ${pnl['research_counterfactual']['net_pnl_usd']:.2f} "
        f"({pnl['research_counterfactual']['filled_and_exited']} filled/exited of {pnl['research_counterfactual']['trajectories']})",
        f"- round-trip friction per contract: ${pnl['round_trip_friction_usd']:.2f} ({pnl['friction_model']})",
        "",
    ]
    audit = report.get("bar_time_audit") or {}
    lines.append("## Bar-time audit (deterministic, from vaulted snapshots)")
    lines.append("")
    lines.append(
        f"- snapshots checked: **{audit.get('snapshots_checked', 0)}** | "
        f"unsound: **{audit.get('unsound', 0)}**"
    )
    for item in audit.get("detail", []):
        if item.get("status") != "PASS":
            lines.append(f"  - `{item.get('snapshot_id')}` — {', '.join(item.get('irregularities') or [])}")
    lines.append("")

    calibration = report.get("calibration_trade") or {}
    lines.append("## Calibration trade (machinery validation — excluded from performance)")
    lines.append("")
    if calibration.get("status") == "COMPLETED":
        entry = calibration.get("entry") or {}
        exit_record = calibration.get("exit") or {}
        lines.append(
            f"- **{calibration['status']}** {entry.get('symbol')} {entry.get('option_type')} "
            f"{entry.get('strike')} exp {entry.get('expiration_date')} (band ${entry.get('premium_band')}): "
            f"entry ask {entry.get('entry_ask')} → exit bid {exit_record.get('exit_bid')} "
            f"({exit_record.get('holding_minutes')} min, {exit_record.get('exit_reason')})"
        )
        lines.append(
            f"- gross ${calibration['gross_pnl_usd']:.2f} − frozen friction "
            f"${calibration['friction_usd']:.2f} = **net ${calibration['net_pnl_usd']:.2f}**"
        )
        if calibration.get("net_pnl_usd_fees_only") is not None:
            lines.append(
                f"- net deducting only fees (gross is ask-to-bid, so spread and "
                f"decay are already inside it): "
                f"**${calibration['net_pnl_usd_fees_only']:.2f}**"
            )
        hurdle = calibration.get("entry_cost_hurdle")
        if hurdle:
            lines.append(
                f"- ex-ante hurdle at entry: **{hurdle['total_pct_of_premium']:.2f}% of premium** "
                f"(spread ${hurdle['spread_cost_usd']:.2f} + fees ${hurdle['fee_cost_usd']:.2f} + "
                f"decay ${hurdle['decay_cost_usd']:.2f}) — the frozen constant understates the "
                f"hurdle by **{hurdle['frozen_understated_by_x']}x**"
            )
    else:
        lines.append(f"- status: **{calibration.get('status', 'NO_ENTRY')}**")
    lines.append("")
    failing = [slot for slot in coverage["slots"] if slot["status"] not in ("COMPLETED", None)]
    if failing:
        lines.append("## Slots needing attention")
        lines.append("")
        for slot in failing:
            lines.append(f"- `{slot['run_id']}` — {slot['status']}")
        lines.append("")
    if report["warnings"]:
        lines.append("## Warnings")
        lines.append("")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"> {caveat}")
    lines.append("")
    return "\n".join(lines)


def write_report(report_date: date, *, project_root: Path = ROOT) -> Path:
    report = build_report(report_date, project_root=project_root)
    out_dir = project_root / "logs/eod"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report_date.isoformat()}.pnl.json"
    temporary = json_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    (out_dir / f"{report_date.isoformat()}.report.md").write_text(
        render_markdown(report), encoding="utf-8",
    )
    return json_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Session date YYYY-MM-DD (default: today PT)")
    args = parser.parse_args()
    report_date = (
        date.fromisoformat(args.date)
        if args.date
        else datetime.now(SESSION_TIMEZONE).date()
    )
    path = write_report(report_date)
    print(json.dumps({"status": "WRITTEN", "path": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

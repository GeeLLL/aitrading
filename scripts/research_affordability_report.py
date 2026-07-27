#!/usr/bin/env python3
"""Deterministic affordability & signal-rate research report.

Answers, from REAL recorded data only (no market calls, no LLM arithmetic):

  1. Cost wall — across every option quote this system has ever vaulted or
     logged, which underlyings can produce a contract that fits the frozen
     eligibility gates (|delta| 0.30-0.65, spread <= 5%) under each premium
     cap ($75 stage 1 / $100 stage 2 / $120 absolute / $300 capital)?
  2. Signal rate — the observed distribution of volume_ratio across all
     recorded slots, versus the BASE_25 (2.5) / BASE_30 (3.0) label
     thresholds and the live 1.50 gate.

Pure read-only aggregation for research; PILOT/after-hours caveats are
stamped into the output. Writes research/affordability/<date>.json and .md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import SESSION_TIMEZONE

DELTA_BAND = (Decimal("0.30"), Decimal("0.65"))
MAX_REL_SPREAD = Decimal("0.05")
CAPS = (75, 100, 120, 300)


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _quote_rows_from_envelope(envelope: dict) -> list[dict]:
    """Extract joined quote observations from one vault envelope (defensive)."""
    rows: list[dict] = []
    try:
        results = {r.get("tool"): r for r in envelope["response"]["tool_results"] if isinstance(r, dict)}
    except (KeyError, TypeError):
        return rows
    symbol = None
    request = envelope.get("request")
    if isinstance(request, dict):
        symbol = request.get("symbol")

    # Map instrument_id -> (strike, expiration, type) when instruments present.
    instruments: dict[str, dict] = {}
    inst_out = (results.get("get_option_instruments") or {}).get("output")
    if isinstance(inst_out, dict):
        data = inst_out.get("data", inst_out)
        candidates = data.get("instruments") if isinstance(data, dict) else None
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, dict) and item.get("id"):
                    instruments[str(item["id"])] = item

    quote_out = (results.get("get_option_quotes") or {}).get("output")
    if quote_out is None:
        return rows
    data = quote_out.get("data", quote_out) if isinstance(quote_out, dict) else quote_out
    raw_quotes = None
    if isinstance(data, dict):
        for key in ("results", "quotes"):
            if isinstance(data.get(key), list):
                raw_quotes = data[key]
                break
    elif isinstance(data, list):
        raw_quotes = data
    if not isinstance(raw_quotes, list):
        return rows

    for item in raw_quotes:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else item
        instrument_id = str(quote.get("instrument_id") or item.get("instrument_id") or "")
        inst = instruments.get(instrument_id, {})
        mark = _dec(quote.get("adjusted_mark_price") or quote.get("mark_price") or quote.get("mark"))
        bid = _dec(quote.get("bid_price") or quote.get("bid"))
        ask = _dec(quote.get("ask_price") or quote.get("ask"))
        delta = _dec(quote.get("delta"))
        close = item.get("close") if isinstance(item.get("close"), dict) else {}
        symbol_value = inst.get("chain_symbol") or close.get("symbol") or symbol or "?"
        rows.append({
            "source_symbol": str(symbol_value),
            "instrument_id": instrument_id,
            "strike": inst.get("strike_price"),
            "expiration": inst.get("expiration_date"),
            "option_type": inst.get("type"),
            "mark": mark,
            "bid": bid,
            "ask": ask,
            "delta": delta,
            "volume": quote.get("volume"),
            "open_interest": quote.get("open_interest"),
            "origin": "vault",
        })
    return rows


def _quote_rows_from_options_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            rows.append({
                "source_symbol": str(item.get("chain_symbol") or item.get("symbol") or item.get("underlying") or "?"),
                "instrument_id": str(item.get("instrument_id") or item.get("id") or ""),
                "strike": item.get("strike_price") or item.get("strike"),
                "expiration": item.get("expiration_date") or item.get("expiration"),
                "option_type": item.get("type") or item.get("option_type"),
                "mark": _dec(item.get("adjusted_mark_price") or item.get("mark_price") or item.get("mark")),
                "bid": _dec(item.get("bid_price") or item.get("bid")),
                "ask": _dec(item.get("ask_price") or item.get("ask")),
                "delta": _dec(item.get("delta")),
                "volume": item.get("volume"),
                "open_interest": item.get("open_interest"),
                "origin": path.name,
            })
    except OSError:
        pass
    return rows


def _quote_rows_from_trajectories(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(root.glob("*/*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict) or item.get("event_type") not in ("CANDIDATE", "QUOTE"):
            continue
        rows.append({
            "source_symbol": str(item.get("underlying") or "?"),
            "instrument_id": str(item.get("instrument_id") or ""),
            "strike": item.get("strike"),
            "expiration": item.get("expiration_date"),
            "option_type": item.get("option_type"),
            "mark": _dec(item.get("mark")),
            "bid": _dec(item.get("bid")),
            "ask": _dec(item.get("ask")),
            "delta": _dec(item.get("delta")),
            "volume": item.get("volume"),
            "open_interest": item.get("open_interest"),
            "origin": "trajectory",
        })
    return rows


def analyze_quotes(rows: list[dict]) -> dict:
    per_symbol: dict[str, dict] = {}
    for row in rows:
        mark = row["mark"]
        delta = row["delta"]
        if mark is None or mark <= 0:
            continue
        symbol = row["source_symbol"] or "?"
        bucket = per_symbol.setdefault(symbol, {
            "observations": 0,
            "delta_band": 0,
            "band_premiums_usd": [],
            "band_pass_spread": 0,
            "band_affordable": {str(cap): 0 for cap in CAPS},
            "full_gate_stage1": 0,
        })
        bucket["observations"] += 1
        in_band = delta is not None and DELTA_BAND[0] <= abs(delta) <= DELTA_BAND[1]
        if not in_band:
            continue
        premium = mark * 100
        bucket["delta_band"] += 1
        bucket["band_premiums_usd"].append(float(premium))
        spread_ok = False
        if row["bid"] is not None and row["ask"] is not None and mark > 0:
            spread_ok = (row["ask"] - row["bid"]) / mark <= MAX_REL_SPREAD
        if spread_ok:
            bucket["band_pass_spread"] += 1
        for cap in CAPS:
            if premium <= cap:
                bucket["band_affordable"][str(cap)] += 1
        if spread_ok and premium <= 75:
            bucket["full_gate_stage1"] += 1

    for bucket in per_symbol.values():
        premiums = sorted(bucket.pop("band_premiums_usd"))
        bucket["band_min_premium_usd"] = premiums[0] if premiums else None
        bucket["band_median_premium_usd"] = (
            round(statistics.median(premiums), 2) if premiums else None
        )
    return per_symbol


def analyze_volume_ratios(worker_root: Path) -> dict:
    ratios: list[float] = []
    for path in sorted(worker_root.glob("*/_indicators_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def walk(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == "volume_ratio":
                        parsed = _dec(value)
                        if parsed is not None and parsed >= 0:
                            ratios.append(float(parsed))
                    else:
                        walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(payload)
    if not ratios:
        return {"observations": 0}
    ordered = sorted(ratios)

    def pct(p: float) -> float:
        index = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
        return round(ordered[index], 4)

    return {
        "observations": len(ordered),
        "max": round(ordered[-1], 4),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "p99": pct(0.99),
        "count_ge_1.5": sum(1 for r in ordered if r >= 1.5),
        "count_ge_2.5_BASE_25": sum(1 for r in ordered if r >= 2.5),
        "count_ge_3.0_BASE_30": sum(1 for r in ordered if r >= 3.0),
    }


def build_report(report_date: date) -> dict:
    rows: list[dict] = []
    for path in sorted((ROOT / "logs/raw").glob("*/*.json")):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(envelope, dict):
            rows.extend(_quote_rows_from_envelope(envelope))
    for path in sorted((ROOT / "logs/pilot").glob("*/*.options.jsonl")):
        rows.extend(_quote_rows_from_options_jsonl(path))
    rows.extend(_quote_rows_from_trajectories(ROOT / "logs/quote_trajectories"))

    return {
        "schema_version": 1,
        "date": report_date.isoformat(),
        "generated_at": datetime.now(SESSION_TIMEZONE).isoformat(),
        "gates": {
            "delta_band": [str(DELTA_BAND[0]), str(DELTA_BAND[1])],
            "max_relative_spread": str(MAX_REL_SPREAD),
            "premium_caps_usd": list(CAPS),
        },
        "quote_observations": len([r for r in rows if r["mark"] is not None]),
        "affordability_by_underlying": analyze_quotes(rows),
        "volume_ratio_distribution": analyze_volume_ratios(ROOT / "logs/launchd_worker"),
        "caveats": [
            "Research artifact only — PILOT_EXCLUDED_FROM_PERFORMANCE.",
            "SOFI/RIVN/BAC snapshots were collected after hours 2026-07-27: quotes/spreads may not represent regular-session conditions; strikes and open interest are structural.",
            "volume/open-interest floors not evaluated here where fields were absent from the source record.",
            "No market data was fetched by this script; it aggregates existing local records only.",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# Affordability & signal-rate research — {report['date']}",
        "",
        f"*Generated {report['generated_at']} — research only, not performance evidence*",
        "",
        f"Quote observations analyzed: **{report['quote_observations']}**",
        "",
        "## Cost wall by underlying (delta band 0.30–0.65)",
        "",
        "| Underlying | Obs | In band | Min premium | Median premium | ≤$75 | ≤$100 | ≤$120 | ≤$300 | band+spread+$75 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    afford = report["affordability_by_underlying"]
    for symbol in sorted(afford):
        b = afford[symbol]
        fmt = lambda v: f"${v:.2f}" if isinstance(v, (int, float)) else "—"
        lines.append(
            f"| {symbol} | {b['observations']} | {b['delta_band']} | {fmt(b['band_min_premium_usd'])} "
            f"| {fmt(b['band_median_premium_usd'])} | {b['band_affordable']['75']} "
            f"| {b['band_affordable']['100']} | {b['band_affordable']['120']} "
            f"| {b['band_affordable']['300']} | {b['full_gate_stage1']} |"
        )
    vr = report["volume_ratio_distribution"]
    lines += [
        "",
        "## Volume-ratio distribution vs thresholds",
        "",
        f"- observations: **{vr.get('observations', 0)}**",
        f"- max: {vr.get('max')} | p50: {vr.get('p50')} | p90: {vr.get('p90')} | p99: {vr.get('p99')}",
        f"- ≥1.5 (live gate): {vr.get('count_ge_1.5')} | ≥2.5 (BASE_25): {vr.get('count_ge_2.5_BASE_25')} | ≥3.0 (BASE_30): {vr.get('count_ge_3.0_BASE_30')}",
        "",
    ]
    for caveat in report["caveats"]:
        lines.append(f"> {caveat}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    report_date = (
        date.fromisoformat(args.date) if args.date else datetime.now(SESSION_TIMEZONE).date()
    )
    report = build_report(report_date)
    out_dir = ROOT / "research/affordability"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report_date.isoformat()}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / f"{report_date.isoformat()}.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": "WRITTEN", "path": str(json_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

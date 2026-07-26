"""Deterministic read-only Robinhood collector built on the direct MCP client.

This is the LLM-free counterpart to ``collect_official_raw_snapshot``: instead of
driving the official Robinhood MCP through the Claude CLI and harvesting a
stream-json transcript, it calls the same six read-only tools directly over the
MCP protocol (``execution.mcp_client``) and stores a byte-faithful snapshot in
the same immutable vault. The stored envelope is identical in shape, tagged
``transport: "PYTHON_DIRECT_MCP"`` so a Mac-side A/B run can compare it against
the CLI path before anything is switched over.

The exact tool argument shapes, pagination (``data.next`` cursor), option
expiration/strike selection, and response nesting were taken from real captured
snapshots under ``logs/raw/`` (2026-07-20/21), so this matches observed reality —
but it has NOT been run against the live server from here, and the OAuth token it
needs is acquired interactively on the Mac. Every selection helper is a small
pure function so the logic is unit-tested offline; live confirmation of arg names
against the current tool schema is a Mac-side step.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlparse

from execution.mcp_client import (
    McpClient,
    Transport,
    TokenProvider,
    UrllibTransport,
    tool_result_json,
)
from execution.official_mcp_collector import (
    OfficialCollectorError,
    RAW_REQUIRED_TOOLS,
    SYMBOL_PATTERN,
    _freshest_source_timestamp,
)
from execution.raw_data_vault import RawDataVault, RawSnapshotReceipt
from monitoring.market_calendar import EXCHANGE_TIMEZONE, is_market_open, previous_market_open_date

ROBINHOOD_MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"

BENCHMARKS = ("SPY", "QQQ")
MIN_DTE = 7
MAX_DTE = 21
STRIKE_BAND = Decimal("0.05")   # ±5% of the underlying
MAX_CONTRACTS = 120
QUOTE_BATCH = 50
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested; no I/O).
# --------------------------------------------------------------------------- #

def symbol_set(target: str) -> list[str]:
    """The de-duplicated benchmark-plus-target set, benchmarks first."""
    ordered = list(BENCHMARKS)
    if target not in ordered:
        ordered.append(target)
    return ordered


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def recent_completed_session(now: datetime) -> date:
    """The most recent trading day whose regular session has fully closed."""
    exchange_now = now.astimezone(EXCHANGE_TIMEZONE)
    today = exchange_now.date()
    close_today = exchange_now.replace(
        hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0, microsecond=0
    )
    if is_market_open(today) and exchange_now >= close_today:
        return today
    return previous_market_open_date(today)


def session_window_utc(session_date: date) -> tuple[str, str]:
    """(start, end) ISO-8601 UTC bounds for a regular session, correct across DST."""
    open_et = datetime(
        session_date.year, session_date.month, session_date.day,
        SESSION_OPEN[0], SESSION_OPEN[1], tzinfo=EXCHANGE_TIMEZONE,
    )
    close_et = datetime(
        session_date.year, session_date.month, session_date.day,
        SESSION_CLOSE[0], SESSION_CLOSE[1], tzinfo=EXCHANGE_TIMEZONE,
    )
    fmt = lambda moment: moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return fmt(open_et), fmt(close_et)


def underlying_last_price(equity_quotes_output: Mapping[str, Any], symbol: str) -> Decimal | None:
    """Extract the target's last trade price (fallback: official close) from
    a get_equity_quotes response."""
    data = equity_quotes_output.get("data") if isinstance(equity_quotes_output, Mapping) else None
    results = data.get("results") if isinstance(data, Mapping) else None
    if not isinstance(results, list):
        return None
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        quote = entry.get("quote") if isinstance(entry.get("quote"), Mapping) else {}
        close = entry.get("close") if isinstance(entry.get("close"), Mapping) else {}
        if quote.get("symbol") == symbol or close.get("symbol") == symbol:
            return _to_decimal(quote.get("last_trade_price")) or _to_decimal(close.get("price"))
    return None


def select_expiration(expiration_dates: Iterable[str], today: date, *, min_dte: int = MIN_DTE, max_dte: int = MAX_DTE) -> str | None:
    """Nearest expiration whose days-to-expiry is within [min_dte, max_dte]."""
    best: tuple[int, str] | None = None
    for raw in expiration_dates:
        try:
            exp = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        dte = (exp - today).days
        if min_dte <= dte <= max_dte and (best is None or dte < best[0]):
            best = (dte, str(raw)[:10])
    return best[1] if best else None


def extract_next_cursor(next_value: Any) -> str | None:
    """Pull the ``cursor`` query parameter out of a get_option_instruments
    ``data.next`` pagination URL (or None when there is no next page)."""
    if not isinstance(next_value, str) or not next_value:
        return None
    query = parse_qs(urlparse(next_value).query)
    values = query.get("cursor")
    return values[0] if values else None


def select_instrument_ids(
    instruments: Iterable[Mapping[str, Any]],
    underlying_price: Decimal | None,
    *,
    band: Decimal = STRIKE_BAND,
    cap: int = MAX_CONTRACTS,
) -> list[str]:
    """Instrument ids within ±band of the underlying, nearest-the-money first,
    capped. With no underlying price we cannot bound the slice → empty (the
    caller fails closed rather than pulling the whole chain)."""
    if underlying_price is None or underlying_price <= 0:
        return []
    low = underlying_price * (Decimal(1) - band)
    high = underlying_price * (Decimal(1) + band)
    scored: list[tuple[Decimal, str]] = []
    for inst in instruments:
        if not isinstance(inst, Mapping):
            continue
        strike = _to_decimal(inst.get("strike_price"))
        ident = inst.get("id")
        if strike is None or not isinstance(ident, str) or not ident:
            continue
        if low <= strike <= high:
            scored.append((abs(strike - underlying_price), ident))
    scored.sort(key=lambda row: row[0])
    return [ident for _distance, ident in scored[:cap]]


def batches(items: list[str], size: int = QUOTE_BATCH) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)] or []


# --------------------------------------------------------------------------- #
# Orchestration (tested with a fake client) + public entry point.
# --------------------------------------------------------------------------- #

def collect_via_client(client: McpClient, symbol: str, now: datetime) -> tuple[list[dict], list[dict], list[str]]:
    """Run the six read-only tools deterministically; return (requests,
    responses, response_texts) in the same shape the CLI harvester produces."""
    import json as _json

    requests: list[dict] = []
    responses: list[dict] = []
    response_texts: list[str] = []

    def call(tool: str, arguments: Mapping[str, Any]) -> Any:
        output = tool_result_json(client.call_tool(tool, arguments))
        requests.append({"tool": tool, "input": dict(arguments)})
        responses.append({"tool": tool, "output": output})
        response_texts.append(_json.dumps(output))
        return output

    symbols = symbol_set(symbol)
    session_date = recent_completed_session(now)
    start, end = session_window_utc(session_date)

    equity_quotes = call("get_equity_quotes", {"symbols": symbols})
    call("get_equity_historicals", {
        "symbols": symbols, "interval": "5minute", "bounds": "regular",
        "start_time": start, "end_time": end,
    })

    chains_output = call("get_option_chains", {"underlying_symbol": symbol})
    chain = _first_chain(chains_output)
    if chain is None:
        raise OfficialCollectorError("DIRECT_NO_OPTION_CHAIN")
    chain_id = chain.get("id")
    expiration = select_expiration(chain.get("expiration_dates") or [], session_date)
    if not isinstance(chain_id, str) or not chain_id or not expiration:
        raise OfficialCollectorError("DIRECT_NO_EXPIRATION_IN_WINDOW")

    instruments: list[Mapping[str, Any]] = []
    cursor: str | None = None
    for _page in range(20):  # hard page cap; the bounded slice is small
        args = {"chain_id": chain_id, "expiration_dates": expiration, "state": "active"}
        if cursor:
            args["cursor"] = cursor
        page = call("get_option_instruments", args)
        data = page.get("data") if isinstance(page, Mapping) else {}
        instruments.extend(data.get("instruments") or [])
        cursor = extract_next_cursor(data.get("next"))
        if not cursor:
            break

    underlying = underlying_last_price(equity_quotes, symbol)
    instrument_ids = select_instrument_ids(instruments, underlying)
    if not instrument_ids:
        raise OfficialCollectorError("DIRECT_NO_INSTRUMENTS_IN_STRIKE_BAND")
    for batch in batches(instrument_ids):
        call("get_option_quotes", {"instrument_ids": batch})

    call("get_earnings_results", {"symbol": symbol})

    called = {request["tool"] for request in requests}
    missing = RAW_REQUIRED_TOOLS - called
    if missing:
        raise OfficialCollectorError("DIRECT_INCOMPLETE:" + ",".join(sorted(missing)))
    return requests, responses, response_texts


def _first_chain(chains_output: Any) -> Mapping[str, Any] | None:
    data = chains_output.get("data") if isinstance(chains_output, Mapping) else None
    chains = data.get("chains") if isinstance(data, Mapping) else None
    if isinstance(chains, list) and chains and isinstance(chains[0], Mapping):
        return chains[0]
    return None


def collect_official_raw_snapshot_direct(
    symbol: str,
    *,
    token_provider: TokenProvider,
    endpoint: str = ROBINHOOD_MCP_ENDPOINT,
    transport: Transport | None = None,
    project_root: str | Path = ".",
    vault_root: str | Path = "logs/raw",
    now: datetime | None = None,
) -> RawSnapshotReceipt:
    """LLM-free raw snapshot: call the read-only MCP tools directly and store the
    result in the immutable vault (transport ``PYTHON_DIRECT_MCP``)."""
    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise OfficialCollectorError("Invalid equity symbol.")
    moment = now or datetime.now(timezone.utc)
    root = Path(project_root).resolve()

    client = McpClient(endpoint, transport or UrllibTransport(), token_provider)
    client.initialize()
    requests, responses, response_texts = collect_via_client(client, normalized_symbol, moment)

    received_at = datetime.now(timezone.utc)
    return RawDataVault(root / vault_root).store(
        source="ROBINHOOD_OFFICIAL_MCP",
        request={
            "schema_version": 1,
            "transport": "PYTHON_DIRECT_MCP",
            "symbol": normalized_symbol,
            "tool_calls": requests,
            "partial": False,
        },
        response={"tool_results": responses},
        source_updated_at=_freshest_source_timestamp(response_texts, not_after=received_at),
        received_at=received_at,
    )

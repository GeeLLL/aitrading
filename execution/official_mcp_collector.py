from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from execution.shadow_input import load_shadow_input
from execution.raw_data_vault import RawDataVault, RawSnapshotReceipt


class OfficialCollectorError(RuntimeError):
    pass


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# The MCP server must be registered in Claude Code under this exact name
# (claude mcp add robinhood-trading ...), and its OAuth must be completed once
# interactively before unattended runs.
MCP_SERVER_NAME = "robinhood-trading"

# Defense in depth for unattended collection.  Claude Code print mode denies
# every tool that is not explicitly allowed, so the child agent can call only
# these read-only Robinhood tools; order/review/cancel/mutation tools are never
# allowed, and local file/shell/network tools are explicitly disallowed again.
READ_ONLY_ROBINHOOD_TOOLS = (
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_option_positions",
    "get_equity_orders",
    "get_option_orders",
    "get_equity_quotes",
    "get_equity_historicals",
    "get_equity_technical_indicators",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
    "get_earnings_calendar",
)

EXPLICITLY_DISALLOWED_TOOLS = (
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
)

# The raw-snapshot collector gets an even narrower allowlist: market data only.
# Account/portfolio/order/position tools are excluded so no account identifier
# can ever enter the harvested stream; the vault's forbidden-key scan then
# provides a second, independent layer.
#
# Earnings uses get_earnings_results (symbol-scoped, trailing 8 quarters, small)
# and NOT get_earnings_calendar: the calendar tool has no symbol parameter, so
# it returns a market-wide window that overflows the harness tool-output cap and
# fails the run closed (observed live 2026-07-21 06:10 canary).
RAW_COLLECTOR_TOOLS = (
    "get_equity_quotes",
    "get_equity_historicals",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
    "get_earnings_results",
)

# Every one of these must appear in the harvested stream at least once or the
# snapshot is incomplete and the run fails closed.
RAW_REQUIRED_TOOLS = frozenset(RAW_COLLECTOR_TOOLS)


def claude_binary() -> str:
    """Locate the Claude Code CLI; unattended collection fails closed without it."""

    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        Path.home() / ".claude/local/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        Path.home() / ".local/bin/claude",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise OfficialCollectorError("CLAUDE_CLI_NOT_FOUND")


def read_only_allowed_tools() -> str:
    return ",".join(f"mcp__{MCP_SERVER_NAME}__{name}" for name in READ_ONLY_ROBINHOOD_TOOLS)


def raw_collector_allowed_tools() -> str:
    return ",".join(f"mcp__{MCP_SERVER_NAME}__{name}" for name in RAW_COLLECTOR_TOOLS)


def _read_only_collector_command() -> list[str]:
    return [
        claude_binary(),
        "-p",
        "--output-format", "json",
        "--allowedTools", read_only_allowed_tools(),
        "--disallowedTools", ",".join(EXPLICITLY_DISALLOWED_TOOLS),
    ]


def _raw_harvest_command() -> list[str]:
    """Stream-JSON command for the raw collector.

    The agent only *invokes* read-only tools; deterministic local code harvests
    each tool's request and byte-faithful response from the stream, so no
    market data ever round-trips through the model's own output.
    """

    return [
        claude_binary(),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--allowedTools", raw_collector_allowed_tools(),
        "--disallowedTools", ",".join(EXPLICITLY_DISALLOWED_TOOLS),
    ]


def _final_json_payload(stdout: str) -> dict:
    """Extract the agent's final JSON object from claude -p --output-format json."""

    try:
        envelope = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise OfficialCollectorError("Claude runner output is not valid JSON.") from error
    if not isinstance(envelope, dict) or envelope.get("is_error") is not False:
        raise OfficialCollectorError("Claude runner reported an error result.")
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise OfficialCollectorError("Claude runner returned no final message.")
    text = result.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise OfficialCollectorError("Final agent message is not a JSON object.") from error
    if not isinstance(payload, dict):
        raise OfficialCollectorError("Final agent message must be a JSON object.")
    return payload


def _safe_failure_detail(stderr: str | None) -> str:
    """Return a bounded diagnostic without leaking account-like identifiers."""
    if not stderr:
        return "no stderr"
    tail = "\n".join(stderr.splitlines()[-12:])
    tail = re.sub(r"\b\d{8,}\b", "[REDACTED_NUMBER]", tail)
    tail = re.sub(
        r"(?i)(bearer|token|authorization)([^\n]{0,120})",
        r"\1 [REDACTED]",
        tail,
    )
    return tail[-2000:]


# ISO-8601 with an explicit time and offset (Z or +hh:mm); fractional seconds
# beyond microseconds (Robinhood emits nanoseconds) are truncated before parse.
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})"
)


def _parse_iso_aware(text: str) -> datetime | None:
    normalized = text.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    normalized = re.sub(r"\.(\d{6})\d+", r".\1", normalized)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


# Clock-skew allowance when rejecting future-dated timestamps: option and
# earnings payloads legitimately contain FUTURE datetimes (expirations,
# scheduled report times) which must never be mistaken for an observation time.
_MAX_CLOCK_SKEW_SECONDS = 300


def _freshest_source_timestamp(
    response_texts: list[str], *, not_after: datetime | None = None
) -> datetime:
    """Deterministically extract the newest official *observation* timestamp.

    The model no longer supplies `source_updated_at`; it is derived from
    timestamps literally present in the harvested tool responses, so it cannot
    be fabricated. Timestamps later than the local collection time (plus a
    small clock-skew allowance) are schedule fields such as option expirations
    or upcoming earnings dates, never observations, and are ignored. No
    eligible timestamp at all fails closed.
    """

    ceiling = (not_after or datetime.now(timezone.utc)) + timedelta(
        seconds=_MAX_CLOCK_SKEW_SECONDS
    )
    freshest: datetime | None = None
    for text in response_texts:
        for match in _ISO_TIMESTAMP.findall(text):
            parsed = _parse_iso_aware(match)
            if parsed is None or parsed > ceiling:
                continue
            if freshest is None or parsed > freshest:
                freshest = parsed
    if freshest is None:
        raise OfficialCollectorError(
            "No official past-or-present source timestamp present in harvested responses."
        )
    return freshest


def _result_text(content: object) -> str:
    """Extract the raw text of a tool_result; unknown shapes fail closed."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if parts:
            return "".join(parts)
    raise OfficialCollectorError("Unsupported tool_result content shape.")


def _harvest_stream(stdout: str, expected_prefix: str) -> tuple[list[dict], list[dict], list[str]]:
    """Parse claude stream-json output into ordered (request, response) pairs.

    This is the security boundary for raw collection. Every line must be JSON;
    every Robinhood tool call must have exactly one non-error result whose text
    parses as JSON (a disk-spill/truncation notice does not, and fails closed);
    the terminal result line must report success.
    """

    tool_calls: list[dict] = []          # ordered {id, tool, input}
    results_by_id: dict[str, object] = {}
    response_texts: list[str] = []
    terminal: dict | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise OfficialCollectorError("Claude stream output contained a non-JSON line.") from error
        if not isinstance(event, dict):
            raise OfficialCollectorError("Claude stream line is not an object.")
        kind = event.get("type")
        if kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                if not name.startswith("mcp__"):
                    continue  # harness-internal tools (e.g. ToolSearch)
                if not name.startswith(expected_prefix):
                    raise OfficialCollectorError(f"Unexpected MCP tool in stream: {name}")
                tool_calls.append({
                    "id": str(block.get("id") or ""),
                    "tool": name[len(expected_prefix):],
                    "input": block.get("input"),
                })
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                results_by_id[str(block.get("tool_use_id") or "")] = block
        elif kind == "result":
            terminal = event
    if terminal is None:
        raise OfficialCollectorError("Claude stream ended without a terminal result event.")
    if terminal.get("subtype") != "success" or terminal.get("is_error") is not False:
        raise OfficialCollectorError("Claude runner reported an unsuccessful terminal result.")
    if not tool_calls:
        raise OfficialCollectorError("No official MCP tool calls were harvested.")
    requests: list[dict] = []
    responses: list[dict] = []
    for call in tool_calls:
        block = results_by_id.get(call["id"])
        if block is None:
            raise OfficialCollectorError(f"Tool call {call['tool']} has no result in the stream.")
        if isinstance(block, dict) and block.get("is_error"):
            raise OfficialCollectorError(f"Tool call {call['tool']} returned an error result.")
        text = _result_text(block.get("content") if isinstance(block, dict) else None)
        try:
            output = json.loads(text)
        except json.JSONDecodeError as error:
            raise OfficialCollectorError(
                f"Tool result for {call['tool']} is not valid JSON (possible truncation)."
            ) from error
        if not isinstance(output, (dict, list)):
            raise OfficialCollectorError(f"Tool result for {call['tool']} is not a JSON object or array.")
        requests.append({"tool": call["tool"], "input": call["input"]})
        responses.append({"tool": call["tool"], "output": output})
        response_texts.append(text)
    called = {call["tool"] for call in tool_calls}
    missing = RAW_REQUIRED_TOOLS - called
    if missing:
        raise OfficialCollectorError(
            "Harvested snapshot is incomplete; missing tools: " + ",".join(sorted(missing))
        )
    return requests, responses, response_texts


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise OfficialCollectorError("Raw source timestamp is missing.")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise OfficialCollectorError("Raw source timestamp is invalid.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialCollectorError("Raw source timestamp must include timezone.")
    return parsed


def collect_official_raw_snapshot(
    symbol: str,
    *,
    project_root: str | Path = ".",
    vault_root: str | Path = "logs/raw",
    timeout_seconds: int = 300,
) -> RawSnapshotReceipt:
    """Collect transport-only official MCP data; no model feature calculation.

    The sub-agent only issues bounded read-only tool calls. Deterministic local
    code harvests each call's request and byte-faithful response from the
    stream-json transcript, derives `source_updated_at` from timestamps
    literally present in the responses, and stores the envelope in the
    immutable vault. The model never re-types market data and cannot fabricate
    any stored value; every irregularity fails closed.
    """

    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise OfficialCollectorError("Invalid equity symbol.")
    root = Path(project_root).resolve()
    prompt = (root / "prompts/robinhood_raw_collector.md").read_text(encoding="utf-8").format(symbol=normalized_symbol)
    command = _raw_harvest_command()
    try:
        completed = subprocess.run(
            command, input=prompt, text=True, cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OfficialCollectorError("Official raw MCP collector failed or timed out.") from error
    if completed.returncode != 0:
        raise OfficialCollectorError(
            "Official raw MCP collector returned no valid result. "
            f"exit={completed.returncode}; "
            f"{_safe_failure_detail(getattr(completed, 'stderr', None))}"
        )
    expected_prefix = f"mcp__{MCP_SERVER_NAME}__"
    requests, responses, response_texts = _harvest_stream(completed.stdout, expected_prefix)
    received_at = datetime.now(timezone.utc)
    return RawDataVault(root / vault_root).store(
        source="ROBINHOOD_OFFICIAL_MCP",
        request={
            "schema_version": 1,
            "transport": "CLAUDE_STREAM_JSON_HARVEST",
            "symbol": normalized_symbol,
            "tool_calls": requests,
        },
        response={"tool_results": responses},
        source_updated_at=_freshest_source_timestamp(response_texts, not_after=received_at),
        received_at=received_at,
    )


def collect_official_shadow_snapshot(
    symbol: str,
    output_path: str | Path,
    *,
    project_root: str | Path = ".",
    timeout_seconds: int = 180,
) -> Path:
    """Legacy normalized pilot collector.

    Formal Shadow evidence must use ``collect_official_raw_snapshot`` followed
    by deterministic local feature construction. This compatibility path is
    retained only for existing read-only pilot drills.
    """

    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise OfficialCollectorError("Invalid equity symbol.")
    root = Path(project_root).resolve()
    prompt_path = root / "prompts/robinhood_shadow_collector.md"
    prompt = prompt_path.read_text(encoding="utf-8").format(symbol=normalized_symbol)

    command = _read_only_collector_command()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OfficialCollectorError("Official MCP collector failed to start or timed out.") from error
    if completed.returncode != 0:
        raise OfficialCollectorError(
            "Official MCP collector returned no valid result. "
            f"exit={completed.returncode}; "
            f"{_safe_failure_detail(getattr(completed, 'stderr', None))}"
        )

    normalized = _final_json_payload(completed.stdout)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Parsing is the security boundary: secrets and malformed/unknown fields
    # reject here, before the temporary file can become the destination.
    try:
        load_shadow_input(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)
    return destination

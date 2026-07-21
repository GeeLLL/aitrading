from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from execution.shadow_input import load_shadow_input
from execution.raw_data_vault import RawDataVault, RawSnapshotReceipt


class OfficialCollectorError(RuntimeError):
    pass


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Defense in depth for unattended collection.  The child Codex process sees
# only these read-only Robinhood tools; order/review/cancel/mutation tools are
# not exposed at all.  Each visible tool is then explicitly approved so a
# non-interactive ``codex exec`` run cannot stall on an MCP permission prompt.
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


def _read_only_mcp_overrides() -> list[str]:
    quoted = ",".join(json.dumps(name) for name in READ_ONLY_ROBINHOOD_TOOLS)
    values = [
        "-c",
        f"mcp_servers.robinhood-trading.enabled_tools=[{quoted}]",
    ]
    for name in READ_ONLY_ROBINHOOD_TOOLS:
        values.extend(
            [
                "-c",
                f'mcp_servers.robinhood-trading.tools.{name}.approval_mode="approve"',
            ]
        )
    return values


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
    timeout_seconds: int = 180,
) -> RawSnapshotReceipt:
    """Collect transport-only official MCP data; no model feature calculation."""

    normalized_symbol = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise OfficialCollectorError("Invalid equity symbol.")
    root = Path(project_root).resolve()
    prompt = (root / "prompts/robinhood_raw_collector.md").read_text(encoding="utf-8").format(symbol=normalized_symbol)
    schema = root / "config/raw_mcp_snapshot.schema.json"
    with tempfile.TemporaryDirectory(prefix="robinhood-raw-") as directory:
        result_path = Path(directory) / "raw.json"
        command = [
            "codex", "exec", "-", "--ephemeral", "--color", "never",
            "--sandbox", "read-only", "--cd", str(root),
            "--output-schema", str(schema), "--output-last-message", str(result_path),
            *_read_only_mcp_overrides(),
        ]
        try:
            completed = subprocess.run(
                command, input=prompt, text=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OfficialCollectorError("Official raw MCP collector failed or timed out.") from error
        if completed.returncode != 0 or not result_path.exists():
            raise OfficialCollectorError(
                "Official raw MCP collector returned no valid result. "
                f"exit={completed.returncode}; "
                f"{_safe_failure_detail(getattr(completed, 'stderr', None))}"
            )
        try:
            envelope = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OfficialCollectorError("Official raw MCP result is invalid JSON.") from error
        if envelope.get("schema_version") != 1:
            raise OfficialCollectorError("Unsupported raw snapshot schema.")
        try:
            request = json.loads(envelope.get("request", ""))
            response = json.loads(envelope.get("response", ""))
        except (TypeError, json.JSONDecodeError) as error:
            raise OfficialCollectorError(
                "Raw request/response strings must contain valid JSON."
            ) from error
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise OfficialCollectorError("Raw request/response objects are required.")
        return RawDataVault(root / vault_root).store(
            source="ROBINHOOD_OFFICIAL_MCP",
            request=request,
            response=response,
            source_updated_at=_aware_datetime(envelope.get("source_updated_at")),
            received_at=datetime.now(timezone.utc),
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
    schema_path = root / "config/shadow_input.schema.json"
    prompt = prompt_path.read_text(encoding="utf-8").format(symbol=normalized_symbol)

    with tempfile.TemporaryDirectory(prefix="robinhood-shadow-") as directory:
        result_path = Path(directory) / "snapshot.json"
        command = [
            "codex", "exec", "-", "--ephemeral", "--color", "never",
            "--sandbox", "read-only", "--cd", str(root),
            "--output-schema", str(schema_path),
            "--output-last-message", str(result_path),
            *_read_only_mcp_overrides(),
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OfficialCollectorError("Official MCP collector failed to start or timed out.") from error
        if completed.returncode != 0 or not result_path.exists():
            raise OfficialCollectorError(
                "Official MCP collector returned no valid result. "
                f"exit={completed.returncode}; "
                f"{_safe_failure_detail(getattr(completed, 'stderr', None))}"
            )

        # Parsing is the security boundary: secrets and malformed/unknown fields reject here.
        load_shadow_input(result_path)
        normalized = json.loads(result_path.read_text(encoding="utf-8"))
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(destination)
    return destination

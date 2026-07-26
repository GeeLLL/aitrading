#!/usr/bin/env python3
"""Mac-side entry point for the LLM-free direct MCP collector.

Reads a Robinhood MCP bearer token from the environment and collects one raw
snapshot directly over the MCP protocol (no Claude CLI in the path). Intended for
the A/B comparison against the existing CLI collector before any switch-over.

    export ROBINHOOD_MCP_TOKEN="<bearer token from the MCP OAuth flow>"
    python3 scripts/run_direct_collector.py SPY

Prints the stored snapshot path and SHA-256. Fails closed on any error. This is a
live-brokerage read; run it only on the Mac where the OAuth token was issued,
never from a shared/cloud host.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.mcp_client import EnvTokenProvider, McpError
from execution.official_mcp_collector import OfficialCollectorError
from execution.robinhood_direct_collector import collect_official_raw_snapshot_direct


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_direct_collector.py SYMBOL", file=sys.stderr)
        return 2
    symbol = sys.argv[1]
    try:
        receipt = collect_official_raw_snapshot_direct(
            symbol,
            token_provider=EnvTokenProvider("ROBINHOOD_MCP_TOKEN"),
            project_root=ROOT,
        )
    except (OfficialCollectorError, McpError) as error:
        print(f"FAILED_CLOSED: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(f"COMPLETED transport=PYTHON_DIRECT_MCP symbol={symbol.upper()}")
    print(f"  snapshot: {receipt.path}")
    print(f"  sha256  : {receipt.content_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

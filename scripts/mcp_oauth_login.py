#!/usr/bin/env python3
"""One-time interactive OAuth login for the direct MCP collector (Mac only).

Runs the full MCP authorization flow — discovery, dynamic client registration,
browser consent with PKCE, token exchange — and stores the resulting tokens in
the git-ignored cache (state/secrets/robinhood_mcp_oauth.json, chmod 600).
After this, unattended code uses OAuthTokenProvider, which silently refreshes
and NEVER opens a browser; when refresh stops working, run this script again.

    python3 scripts/mcp_oauth_login.py
    # a browser window opens; approve read-only access; done.

Run this only on the Mac where collection runs, never on a shared host.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.mcp_client import McpError
from execution.mcp_oauth import (
    TokenCache,
    build_authorize_url,
    discover,
    exchange_code,
    pkce_pair,
    register_client,
)
from execution.robinhood_direct_collector import ROBINHOOD_MCP_ENDPOINT

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8721   # fixed so the registered redirect_uri stays stable
TIMEOUT_SECONDS = 300


def _wait_for_code(expected_state: str) -> str:
    """Serve one loopback callback request and return the authorization code."""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API name)
            query = parse_qs(urlparse(self.path).query)
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            ok = bool(code) and state == expected_state
            if ok:
                result["code"] = code
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = (
                "Authorization received - you can close this tab."
                if ok else "Authorization failed (missing code or state mismatch)."
            )
            self.wfile.write(f"<html><body><h2>{message}</h2></body></html>".encode())
            done.set()

        def log_message(self, *args: object) -> None:  # silence request logging
            pass

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(TIMEOUT_SECONDS):
            raise McpError("OAUTH_CALLBACK_TIMEOUT")
    finally:
        server.shutdown()
    code = result.get("code")
    if not code:
        raise McpError("OAUTH_CALLBACK_INVALID")
    return code


def main() -> int:
    redirect_uri = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
    print(f"Discovering authorization metadata for {ROBINHOOD_MCP_ENDPOINT} ...")
    metadata = discover(ROBINHOOD_MCP_ENDPOINT)
    print(f"  authorize: {metadata.authorization_endpoint}")
    print(f"  token    : {metadata.token_endpoint}")

    cache = TokenCache()
    stored = cache.load() or {}
    client_id = str(stored.get("client_id") or "")
    if not client_id:
        print("Registering public client (RFC 7591) ...")
        client_id = register_client(metadata, redirect_uri)
        print(f"  client_id: {client_id}")

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(metadata, client_id, redirect_uri, challenge, state)
    print("Opening browser for consent (approve READ-ONLY access) ...")
    webbrowser.open(url)
    print(f"  waiting for callback on {redirect_uri} (up to {TIMEOUT_SECONDS}s)")
    code = _wait_for_code(state)

    print("Exchanging code for tokens ...")
    tokens = exchange_code(metadata, client_id, redirect_uri, code, verifier)
    cache.save(client_id=client_id, tokens=tokens, resource=metadata.resource)
    print(json.dumps({
        "status": "AUTHORIZED",
        "cache": str(cache.path),
        "has_refresh_token": tokens.refresh_token is not None,
        "expires_at": tokens.expires_at,
    }, indent=2))
    print("\nNext: python3 scripts/run_direct_collector.py SPY")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except McpError as error:
        print(f"FAILED_CLOSED: {error}", file=sys.stderr)
        raise SystemExit(2)

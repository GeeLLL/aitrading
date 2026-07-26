"""Minimal MCP client over Streamable HTTP + JSON-RPC 2.0.

This is the deterministic replacement for driving the official Robinhood MCP
through the Claude CLI: it speaks the Model Context Protocol directly, with no
LLM anywhere in the transport path. Removing the LLM removes a large,
externally-versioned, non-deterministic failure surface (the CLI's stream-json
format, model behavior, rate limits) from a job that is really just "call six
fixed read-only tools."

Design for offline verifiability: the network transport and the auth token are
injected seams (``Transport`` and ``TokenProvider``), so all of the protocol
logic — the initialize handshake, session handling, JSON-vs-SSE response parsing,
error mapping — is fully unit-testable against a fake transport. The only parts
that actually touch the network are the concrete ``UrllibTransport`` and the
OAuth token acquisition, and the latter is an interactive, Mac-side step that
cannot (and should not) run from a cloud sandbox against a live brokerage.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_CLIENT_INFO = {"name": "ge-aitrading-direct", "version": "1.0"}


class McpError(RuntimeError):
    """Any protocol/transport failure. Fail closed on all of them."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    text: str


class Transport(Protocol):
    """One blocking POST. Injected so protocol logic is testable offline."""

    def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> HttpResponse:
        ...


class TokenProvider(Protocol):
    """Yields a bearer token. The OAuth acquisition lives behind this seam."""

    def token(self) -> str:
        ...


class StaticTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        if not self._token:
            raise McpError("EMPTY_BEARER_TOKEN")
        return self._token


class EnvTokenProvider:
    """Read the bearer token from an environment variable at call time."""

    def __init__(self, variable: str = "ROBINHOOD_MCP_TOKEN") -> None:
        self._variable = variable

    def token(self) -> str:
        value = os.environ.get(self._variable) or ""
        if not value:
            raise McpError(f"MISSING_TOKEN_ENV:{self._variable}")
        return value


class UrllibTransport:
    """Concrete stdlib transport. Not unit-tested here (it hits the network);

    all protocol logic that consumes it is tested via a fake transport instead.
    """

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout = timeout_seconds

    def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> HttpResponse:
        request = urllib_request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return HttpResponse(
                    status=response.status,
                    headers={k: v for k, v in response.headers.items()},
                    text=raw,
                )
        except urllib_error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace") if error.fp else ""
            return HttpResponse(status=error.code, headers=dict(error.headers or {}), text=raw)
        except (urllib_error.URLError, OSError, TimeoutError) as error:
            raise McpError(f"TRANSPORT_FAILURE:{type(error).__name__}") from error


def _extract_jsonrpc(response: HttpResponse) -> dict[str, Any]:
    """Parse a JSON-RPC message from a JSON or text/event-stream response body."""
    content_type = ""
    for key, value in response.headers.items():
        if key.lower() == "content-type":
            content_type = value.lower()
            break

    if "text/event-stream" in content_type:
        # Concatenate SSE data: lines per event; return the last JSON-RPC object
        # that carries a result/error (the response to our request).
        message: dict[str, Any] | None = None
        data_lines: list[str] = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            elif not line.strip():
                if data_lines:
                    try:
                        candidate = json.loads("".join(data_lines))
                    except json.JSONDecodeError:
                        candidate = None
                    if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                        message = candidate
                data_lines = []
        if data_lines:
            try:
                candidate = json.loads("".join(data_lines))
                if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                    message = candidate
            except json.JSONDecodeError:
                pass
        if message is None:
            raise McpError("NO_JSONRPC_MESSAGE_IN_EVENT_STREAM")
        return message

    try:
        parsed = json.loads(response.text)
    except json.JSONDecodeError as error:
        raise McpError("RESPONSE_NOT_JSON") from error
    if not isinstance(parsed, dict):
        raise McpError("RESPONSE_NOT_JSONRPC_OBJECT")
    return parsed


class McpClient:
    def __init__(
        self,
        endpoint: str,
        transport: Transport,
        token_provider: TokenProvider,
        *,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        client_info: Mapping[str, Any] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._transport = transport
        self._token_provider = token_provider
        self._protocol_version = protocol_version
        self._client_info = dict(client_info or DEFAULT_CLIENT_INFO)
        self._session_id: str | None = None
        self._request_id = 0
        self._initialized = False

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token_provider.token()}",
            "MCP-Protocol-Version": self._protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _send(self, payload: dict[str, Any], *, expect_response: bool) -> dict[str, Any] | None:
        body = json.dumps(payload).encode("utf-8")
        response = self._transport.post(self._endpoint, body, self._headers())
        if response.status in (401, 403):
            raise McpError("UNAUTHORIZED", code=response.status)
        for key, value in response.headers.items():
            if key.lower() == "mcp-session-id" and value:
                self._session_id = value
        if not expect_response:
            # Notifications: server returns 202 Accepted (or 200) with no body.
            if response.status not in (200, 202, 204):
                raise McpError(f"NOTIFICATION_HTTP_{response.status}", code=response.status)
            return None
        if response.status != 200:
            raise McpError(f"HTTP_{response.status}", code=response.status)
        message = _extract_jsonrpc(response)
        if "error" in message and message["error"] is not None:
            error = message["error"]
            code = error.get("code") if isinstance(error, dict) else None
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise McpError(f"RPC_ERROR:{detail}", code=code)
        if "result" not in message:
            raise McpError("RPC_RESPONSE_MISSING_RESULT")
        result = message["result"]
        if not isinstance(result, dict):
            raise McpError("RPC_RESULT_NOT_OBJECT")
        return result

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def initialize(self) -> dict[str, Any]:
        result = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self._protocol_version,
                    "capabilities": {},
                    "clientInfo": self._client_info,
                },
            },
            expect_response=True,
        )
        # Complete the handshake with the required initialized notification.
        self._send(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_response=False,
        )
        self._initialized = True
        return result or {}

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._send(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}},
            expect_response=True,
        )
        tools = (result or {}).get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call one tool. Returns the raw MCP tool result ({content, isError})."""
        result = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            },
            expect_response=True,
        ) or {}
        if result.get("isError"):
            raise McpError(f"TOOL_ERROR:{name}")
        return result


def tool_result_json(result: Mapping[str, Any]) -> Any:
    """Extract and JSON-parse the text content of an MCP tool result.

    MCP tool results carry a ``content`` list of blocks; the Robinhood tools
    return a single text block whose text is a JSON document. A block that is not
    present or does not parse fails closed (mirrors the CLI harvester's contract).
    """
    content = result.get("content")
    if not isinstance(content, list):
        raise McpError("TOOL_RESULT_HAS_NO_CONTENT")
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    if not texts:
        raise McpError("TOOL_RESULT_HAS_NO_TEXT_BLOCK")
    joined = "".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError as error:
        raise McpError("TOOL_RESULT_TEXT_NOT_JSON") from error

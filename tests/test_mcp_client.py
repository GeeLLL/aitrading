from __future__ import annotations

import json
import unittest

from execution.mcp_client import (
    EnvTokenProvider,
    HttpResponse,
    McpClient,
    McpError,
    StaticTokenProvider,
    tool_result_json,
)


class FakeTransport:
    """Records POSTs and returns canned responses so protocol logic is testable."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.sent: list[dict] = []

    def post(self, url, body, headers) -> HttpResponse:
        payload = json.loads(body.decode("utf-8"))
        self.sent.append({"url": url, "payload": payload, "headers": dict(headers)})
        return self._responder(payload)


def _json_response(payload: dict, *, status: int = 200, headers=None) -> HttpResponse:
    base = {"Content-Type": "application/json"}
    base.update(headers or {})
    return HttpResponse(status=status, headers=base, text=json.dumps(payload))


def _ok_result(request_id, result) -> HttpResponse:
    return _json_response({"jsonrpc": "2.0", "id": request_id, "result": result})


ENDPOINT = "https://example.test/mcp"


class InitializeTests(unittest.TestCase):
    def test_handshake_sends_initialize_then_initialized(self) -> None:
        def responder(payload):
            if payload.get("method") == "initialize":
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-123"},
                    text=json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-06-18"}}),
                )
            return HttpResponse(status=202, headers={}, text="")

        transport = FakeTransport(responder)
        client = McpClient(ENDPOINT, transport, StaticTokenProvider("tok"))
        result = client.initialize()
        self.assertEqual("2025-06-18", result["protocolVersion"])
        # Two POSTs: initialize (with an id) then the initialized notification (no id).
        self.assertEqual("initialize", transport.sent[0]["payload"]["method"])
        self.assertEqual("notifications/initialized", transport.sent[1]["payload"]["method"])
        self.assertNotIn("id", transport.sent[1]["payload"])

    def test_session_id_and_auth_headers_are_sent(self) -> None:
        def responder(payload):
            if payload.get("method") == "initialize":
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json", "Mcp-Session-Id": "sess-xyz"},
                    text=json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {}}),
                )
            if payload.get("method") == "tools/list":
                return _ok_result(payload["id"], {"tools": []})
            return HttpResponse(status=202, headers={}, text="")

        transport = FakeTransport(responder)
        client = McpClient(ENDPOINT, transport, StaticTokenProvider("secret-token"))
        client.initialize()
        client.list_tools()
        list_headers = transport.sent[-1]["headers"]
        self.assertEqual("Bearer secret-token", list_headers["Authorization"])
        self.assertEqual("sess-xyz", list_headers["Mcp-Session-Id"])  # captured from init
        self.assertIn("application/json", list_headers["Accept"])
        self.assertIn("text/event-stream", list_headers["Accept"])


class ToolCallTests(unittest.TestCase):
    def _client(self, responder) -> McpClient:
        return McpClient(ENDPOINT, FakeTransport(responder), StaticTokenProvider("tok"))

    def test_call_tool_returns_result(self) -> None:
        payload_out = {"content": [{"type": "text", "text": json.dumps({"data": {"results": []}})}]}
        client = self._client(lambda p: _ok_result(p["id"], payload_out))
        result = client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})
        self.assertEqual(payload_out["content"], result["content"])
        self.assertEqual({"data": {"results": []}}, tool_result_json(result))

    def test_tool_is_error_fails_closed(self) -> None:
        client = self._client(lambda p: _ok_result(p["id"], {"content": [], "isError": True}))
        with self.assertRaisesRegex(McpError, "TOOL_ERROR:get_option_chains"):
            client.call_tool("get_option_chains", {"underlying_symbol": "SPY"})

    def test_event_stream_response_is_parsed(self) -> None:
        body = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\\"ok\\":true}"}]}}\n'
            "\n"
        )

        def responder(_payload):
            return HttpResponse(status=200, headers={"Content-Type": "text/event-stream"}, text=body)

        client = self._client(responder)
        result = client.call_tool("get_earnings_results", {"symbol": "SPY"})
        self.assertEqual({"ok": True}, tool_result_json(result))

    def test_rpc_error_fails_closed(self) -> None:
        def responder(payload):
            return _json_response({"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32601, "message": "no such method"}})

        client = self._client(responder)
        with self.assertRaisesRegex(McpError, "no such method"):
            client.call_tool("bogus", {})

    def test_unauthorized_fails_closed(self) -> None:
        client = self._client(lambda p: HttpResponse(status=401, headers={}, text=""))
        with self.assertRaisesRegex(McpError, "UNAUTHORIZED"):
            client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})

    def test_non_json_response_fails_closed(self) -> None:
        client = self._client(lambda p: HttpResponse(status=200, headers={"Content-Type": "application/json"}, text="<html>500</html>"))
        with self.assertRaisesRegex(McpError, "RESPONSE_NOT_JSON"):
            client.call_tool("get_equity_quotes", {"symbols": ["SPY"]})


class ToolResultJsonTests(unittest.TestCase):
    def test_missing_content_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "NO_CONTENT"):
            tool_result_json({})

    def test_no_text_block_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "NO_TEXT_BLOCK"):
            tool_result_json({"content": [{"type": "image"}]})

    def test_non_json_text_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "NOT_JSON"):
            tool_result_json({"content": [{"type": "text", "text": "not json"}]})


class TokenProviderTests(unittest.TestCase):
    def test_static_empty_token_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "EMPTY_BEARER_TOKEN"):
            StaticTokenProvider("").token()

    def test_env_missing_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "MISSING_TOKEN_ENV:NOPE_TOKEN_VAR"):
            EnvTokenProvider("NOPE_TOKEN_VAR").token()


if __name__ == "__main__":
    unittest.main()

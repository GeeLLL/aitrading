from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from execution.mcp_client import McpError
from execution.mcp_oauth import (
    AuthServerMetadata,
    OAuthTokenProvider,
    TokenCache,
    TokenSet,
    build_authorize_url,
    discover,
    exchange_code,
    pkce_pair,
    refresh,
    register_client,
)

ENDPOINT = "https://agent.example.com/mcp/trading"

META = AuthServerMetadata(
    resource=ENDPOINT,
    authorization_endpoint="https://auth.example.com/authorize",
    token_endpoint="https://auth.example.com/token",
    registration_endpoint="https://auth.example.com/register",
)


class FakeHttp:
    """Programmable HttpFn recording every call."""

    def __init__(self, routes) -> None:
        self.routes = routes  # url -> (status, body) or callable(method, body)
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append({"method": method, "url": url, "body": body, "headers": dict(headers)})
        handler = self.routes.get(url)
        if handler is None:
            return 404, ""
        if callable(handler):
            return handler(method, body)
        return handler


class DiscoveryTests(unittest.TestCase):
    def test_full_rfc9728_plus_rfc8414_chain(self) -> None:
        http = FakeHttp({
            "https://agent.example.com/.well-known/oauth-protected-resource/mcp/trading": (
                200, json.dumps({"resource": ENDPOINT,
                                 "authorization_servers": ["https://auth.example.com"]})),
            "https://auth.example.com/.well-known/oauth-authorization-server": (
                200, json.dumps({
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                })),
        })
        metadata = discover(ENDPOINT, http)
        self.assertEqual(ENDPOINT, metadata.resource)
        self.assertEqual("https://auth.example.com/token", metadata.token_endpoint)
        self.assertEqual("https://auth.example.com/register", metadata.registration_endpoint)

    def test_origin_fallback_when_no_resource_metadata(self) -> None:
        http = FakeHttp({
            "https://agent.example.com/.well-known/oauth-authorization-server": (
                200, json.dumps({
                    "authorization_endpoint": "https://agent.example.com/authorize",
                    "token_endpoint": "https://agent.example.com/token",
                })),
        })
        metadata = discover(ENDPOINT, http)
        self.assertEqual("https://agent.example.com/token", metadata.token_endpoint)
        self.assertIsNone(metadata.registration_endpoint)

    def test_no_metadata_anywhere_fails_closed(self) -> None:
        with self.assertRaisesRegex(McpError, "NO_AUTHORIZATION_SERVER_METADATA"):
            discover(ENDPOINT, FakeHttp({}))


class RegistrationTests(unittest.TestCase):
    def test_registers_public_client(self) -> None:
        def handler(method, body):
            payload = json.loads(body)
            assert payload["token_endpoint_auth_method"] == "none"
            assert payload["grant_types"] == ["authorization_code", "refresh_token"]
            return 201, json.dumps({"client_id": "client-abc"})

        http = FakeHttp({"https://auth.example.com/register": handler})
        client_id = register_client(META, "http://127.0.0.1:8765/callback", http)
        self.assertEqual("client-abc", client_id)
        self.assertEqual("POST", http.calls[0]["method"])

    def test_missing_registration_endpoint_fails_closed(self) -> None:
        bare = AuthServerMetadata(ENDPOINT, "a", "t", None)
        with self.assertRaisesRegex(McpError, "NO_REGISTRATION_ENDPOINT"):
            register_client(bare, "http://127.0.0.1:1/cb", FakeHttp({}))


class PkceAndUrlTests(unittest.TestCase):
    def test_pkce_pair_is_s256_and_well_formed(self) -> None:
        verifier, challenge = pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_\-]+", verifier))
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        self.assertEqual(expected, challenge)

    def test_authorize_url_carries_pkce_state_and_resource(self) -> None:
        url = build_authorize_url(META, "client-abc", "http://127.0.0.1:8765/cb", "CHAL", "STATE1")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(["code"], query["response_type"])
        self.assertEqual(["client-abc"], query["client_id"])
        self.assertEqual(["CHAL"], query["code_challenge"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(["STATE1"], query["state"])
        self.assertEqual([ENDPOINT], query["resource"])  # RFC 8707


class TokenFlowTests(unittest.TestCase):
    def test_exchange_code_posts_correct_form(self) -> None:
        def handler(method, body):
            assert body["grant_type"] == "authorization_code"
            assert body["code"] == "CODE9"
            assert body["code_verifier"] == "VERIF"
            assert body["resource"] == ENDPOINT
            return 200, json.dumps({"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600})

        http = FakeHttp({"https://auth.example.com/token": handler})
        tokens = exchange_code(META, "client-abc", "http://cb", "CODE9", "VERIF", http, now=1000.0)
        self.assertEqual("AT1", tokens.access_token)
        self.assertEqual("RT1", tokens.refresh_token)
        self.assertEqual(4600.0, tokens.expires_at)

    def test_refresh_keeps_old_refresh_token_when_not_rotated(self) -> None:
        http = FakeHttp({"https://auth.example.com/token": (
            200, json.dumps({"access_token": "AT2", "expires_in": 60}))})
        tokens = refresh(META, "client-abc", "RT1", http, now=0.0)
        self.assertEqual("AT2", tokens.access_token)
        self.assertEqual("RT1", tokens.refresh_token)  # preserved

    def test_token_error_fails_closed(self) -> None:
        http = FakeHttp({"https://auth.example.com/token": (400, "{}")})
        with self.assertRaisesRegex(McpError, "OAUTH_TOKEN_HTTP_400"):
            refresh(META, "client-abc", "RT1", http)


class CacheAndProviderTests(unittest.TestCase):
    def test_cache_roundtrip_with_0600_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TokenCache(Path(directory) / "secrets/oauth.json")
            cache.save(client_id="client-abc",
                       tokens=TokenSet("AT", "RT", 2000.0), resource=ENDPOINT)
            mode = cache.path.stat().st_mode & 0o777
            self.assertEqual(0o600, mode)
            stored = cache.load()
            self.assertEqual("AT", stored["access_token"])

    def test_provider_returns_fresh_token_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TokenCache(Path(directory) / "oauth.json")
            cache.save(client_id="c", tokens=TokenSet("AT", "RT", 5000.0), resource=ENDPOINT)
            provider = OAuthTokenProvider(META, cache, FakeHttp({}), clock=lambda: 1000.0)
            self.assertEqual("AT", provider.token())

    def test_provider_refreshes_expired_token_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TokenCache(Path(directory) / "oauth.json")
            cache.save(client_id="c", tokens=TokenSet("OLD", "RT", 1000.0), resource=ENDPOINT)
            http = FakeHttp({"https://auth.example.com/token": (
                200, json.dumps({"access_token": "NEW", "refresh_token": "RT2", "expires_in": 3600}))})
            provider = OAuthTokenProvider(META, cache, http, clock=lambda: 2000.0)
            self.assertEqual("NEW", provider.token())
            self.assertEqual("NEW", cache.load()["access_token"])  # persisted
            self.assertEqual("RT2", cache.load()["refresh_token"])

    def test_provider_without_cache_requires_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = OAuthTokenProvider(
                META, TokenCache(Path(directory) / "missing.json"), FakeHttp({}),
            )
            with self.assertRaisesRegex(McpError, "OAUTH_INTERACTIVE_REQUIRED"):
                provider.token()

    def test_expired_without_refresh_token_requires_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = TokenCache(Path(directory) / "oauth.json")
            cache.save(client_id="c", tokens=TokenSet("AT", None, 1000.0), resource=ENDPOINT)
            provider = OAuthTokenProvider(META, cache, FakeHttp({}), clock=lambda: 2000.0)
            with self.assertRaisesRegex(McpError, "OAUTH_INTERACTIVE_REQUIRED"):
                provider.token()


if __name__ == "__main__":
    unittest.main()

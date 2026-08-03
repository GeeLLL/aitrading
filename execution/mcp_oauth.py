"""OAuth 2.1 client for the direct MCP transport (RFC 8414/7591/7636/8707).

This is the missing half of removing the Claude CLI from the collection
transport: the direct MCP client (``execution.mcp_client``) already speaks the
protocol but needs a bearer token, which until now only the CLI's own OAuth
could supply. This module implements the standard MCP authorization flow:

  1. protected-resource metadata discovery (RFC 9728) on the MCP endpoint,
  2. authorization-server metadata discovery (RFC 8414),
  3. dynamic client registration (RFC 7591, public client, no secret),
  4. authorization-code + PKCE S256 (RFC 7636) with the ``resource`` indicator
     (RFC 8707) the MCP spec requires,
  5. token exchange + refresh, cached on disk with 0600 permissions.

Design for offline verifiability (same policy as mcp_client): all HTTP goes
through an injectable ``http`` callable, so every flow is unit-tested against a
fake. The only genuinely interactive step — the browser consent — is isolated in
``interactive_authorize`` and runs on the Mac (scripts/mcp_oauth_login.py), never
from a cloud sandbox against the live brokerage.

The token cache lives under ``state/secrets/`` which is git-ignored; tokens must
never enter the repo, the vault, or any log.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse

from execution.mcp_client import McpError

# An http seam: (method, url, body, headers) -> (status, body_text).
# body may be None (GET), a Mapping (form-encoded), or a str (raw body, e.g.
# the JSON document of a dynamic-registration request).
HttpFn = Callable[[str, str, Mapping[str, str] | str | None, Mapping[str, str]], tuple[int, str]]

DEFAULT_CLIENT_NAME = "ge-aitrading-direct-collector"
DEFAULT_CACHE_PATH = Path("state/secrets/robinhood_mcp_oauth.json")
_EXPIRY_SKEW_SECONDS = 60


def _urllib_http(method: str, url: str, form: Mapping[str, str] | str | None, headers: Mapping[str, str]) -> tuple[int, str]:
    from urllib import error as urllib_error
    from urllib import request as urllib_request

    if form is None:
        data = None
    elif isinstance(form, str):
        data = form.encode("utf-8")
    else:
        data = urlencode(form).encode("utf-8")
    request = urllib_request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else ""
        return error.code, body
    except (urllib_error.URLError, OSError, TimeoutError) as error:
        raise McpError(f"OAUTH_TRANSPORT_FAILURE:{type(error).__name__}") from error


def _get_json(http: HttpFn, url: str) -> dict[str, Any]:
    status, body = http("GET", url, None, {"Accept": "application/json"})
    if status != 200:
        raise McpError(f"OAUTH_DISCOVERY_HTTP_{status}:{url}")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise McpError(f"OAUTH_DISCOVERY_NOT_JSON:{url}") from error
    if not isinstance(parsed, dict):
        raise McpError(f"OAUTH_DISCOVERY_NOT_OBJECT:{url}")
    return parsed


@dataclass(frozen=True)
class AuthServerMetadata:
    resource: str                 # canonical MCP resource URI (RFC 8707 value)
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None


def discover(mcp_endpoint: str, http: HttpFn = _urllib_http) -> AuthServerMetadata:
    """RFC 9728 protected-resource discovery, then RFC 8414 AS metadata."""
    parsed = urlparse(mcp_endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    resource_path = parsed.path.rstrip("/")

    # Path-aware well-known first (per RFC 9728), then origin fallback.
    resource_metadata: dict[str, Any] | None = None
    for candidate in (
        f"{origin}/.well-known/oauth-protected-resource{resource_path}",
        f"{origin}/.well-known/oauth-protected-resource",
    ):
        try:
            resource_metadata = _get_json(http, candidate)
            break
        except McpError:
            continue

    authorization_servers: list[str] = []
    resource_uri = mcp_endpoint
    if resource_metadata:
        raw_servers = resource_metadata.get("authorization_servers")
        if isinstance(raw_servers, list):
            authorization_servers = [str(server) for server in raw_servers if server]
        resource_uri = str(resource_metadata.get("resource") or mcp_endpoint)
    if not authorization_servers:
        # Legacy fallback: the resource origin doubles as the AS.
        authorization_servers = [origin]

    last_error: McpError | None = None
    for server in authorization_servers:
        server_parsed = urlparse(server)
        server_origin = f"{server_parsed.scheme}://{server_parsed.netloc}"
        server_path = server_parsed.path.rstrip("/")
        for candidate in (
            f"{server_origin}/.well-known/oauth-authorization-server{server_path}",
            f"{server_origin}/.well-known/oauth-authorization-server",
            f"{server_origin}/.well-known/openid-configuration",
        ):
            try:
                metadata = _get_json(http, candidate)
            except McpError as error:
                last_error = error
                continue
            authorization_endpoint = metadata.get("authorization_endpoint")
            token_endpoint = metadata.get("token_endpoint")
            if not authorization_endpoint or not token_endpoint:
                continue
            return AuthServerMetadata(
                resource=resource_uri,
                authorization_endpoint=str(authorization_endpoint),
                token_endpoint=str(token_endpoint),
                registration_endpoint=(
                    str(metadata["registration_endpoint"])
                    if metadata.get("registration_endpoint") else None
                ),
            )
    raise McpError("OAUTH_NO_AUTHORIZATION_SERVER_METADATA") from last_error


def register_client(
    metadata: AuthServerMetadata,
    redirect_uri: str,
    http: HttpFn = _urllib_http,
    *,
    client_name: str = DEFAULT_CLIENT_NAME,
) -> str:
    """RFC 7591 dynamic registration of a public client. Returns client_id."""
    if not metadata.registration_endpoint:
        raise McpError("OAUTH_NO_REGISTRATION_ENDPOINT")
    payload = json.dumps({
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    status, body = http(
        "POST",
        metadata.registration_endpoint,
        payload,
        {"Content-Type": "application/json", "Accept": "application/json"},
    )
    if status not in (200, 201):
        raise McpError(f"OAUTH_REGISTRATION_HTTP_{status}")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise McpError("OAUTH_REGISTRATION_NOT_JSON") from error
    client_id = parsed.get("client_id") if isinstance(parsed, dict) else None
    if not client_id:
        raise McpError("OAUTH_REGISTRATION_MISSING_CLIENT_ID")
    return str(client_id)


def pkce_pair() -> tuple[str, str]:
    """RFC 7636 verifier + S256 challenge (base64url, unpadded)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorize_url(
    metadata: AuthServerMetadata,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    separator = "&" if "?" in metadata.authorization_endpoint else "?"
    return metadata.authorization_endpoint + separator + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": metadata.resource,
    })


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float | None   # unix seconds, None = unknown

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at - _EXPIRY_SKEW_SECONDS


def _token_request(
    metadata: AuthServerMetadata,
    form: dict[str, str],
    http: HttpFn,
    *,
    now: float | None = None,
) -> TokenSet:
    status, body = http(
        "POST",
        metadata.token_endpoint,
        form,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    if status != 200:
        raise McpError(f"OAUTH_TOKEN_HTTP_{status}")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise McpError("OAUTH_TOKEN_NOT_JSON") from error
    access = parsed.get("access_token") if isinstance(parsed, dict) else None
    if not access:
        raise McpError("OAUTH_TOKEN_MISSING_ACCESS_TOKEN")
    expires_in = parsed.get("expires_in")
    moment = now if now is not None else time.time()
    return TokenSet(
        access_token=str(access),
        refresh_token=str(parsed["refresh_token"]) if parsed.get("refresh_token") else None,
        expires_at=(moment + float(expires_in)) if isinstance(expires_in, (int, float)) else None,
    )


def exchange_code(
    metadata: AuthServerMetadata,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    http: HttpFn = _urllib_http,
    *,
    now: float | None = None,
) -> TokenSet:
    return _token_request(metadata, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": metadata.resource,
    }, http, now=now)


def refresh(
    metadata: AuthServerMetadata,
    client_id: str,
    refresh_token: str,
    http: HttpFn = _urllib_http,
    *,
    now: float | None = None,
) -> TokenSet:
    refreshed = _token_request(metadata, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": metadata.resource,
    }, http, now=now)
    if refreshed.refresh_token is None:
        # Servers may omit the rotated refresh token; keep using the old one.
        refreshed = TokenSet(refreshed.access_token, refresh_token, refreshed.expires_at)
    return refreshed


# --------------------------------------------------------------------------- #
# Disk cache (0600; state/secrets is git-ignored) and the TokenProvider.
# --------------------------------------------------------------------------- #

class TokenCache:
    def __init__(self, path: str | Path = DEFAULT_CACHE_PATH) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def save(self, *, client_id: str, tokens: TokenSet, resource: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "client_id": client_id,
            "resource": resource,
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)


class OAuthTokenProvider:
    """TokenProvider backed by the disk cache; refreshes when expired.

    Never interactive: if there is no usable token and no working refresh
    token, it raises ``OAUTH_INTERACTIVE_REQUIRED`` so the caller (a human on
    the Mac) knows to run scripts/mcp_oauth_login.py once. Unattended code can
    therefore fail closed but can never pop a browser.
    """

    def __init__(
        self,
        metadata: AuthServerMetadata,
        cache: TokenCache | None = None,
        http: HttpFn = _urllib_http,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._metadata = metadata
        self._cache = cache or TokenCache()
        self._http = http
        self._clock = clock

    def token(self) -> str:
        stored = self._cache.load()
        if not stored or not stored.get("access_token"):
            raise McpError("OAUTH_INTERACTIVE_REQUIRED:no cached token")
        tokens = TokenSet(
            access_token=str(stored["access_token"]),
            refresh_token=str(stored["refresh_token"]) if stored.get("refresh_token") else None,
            expires_at=float(stored["expires_at"]) if stored.get("expires_at") is not None else None,
        )
        if not tokens.expired(self._clock()):
            return tokens.access_token
        if not tokens.refresh_token:
            raise McpError("OAUTH_INTERACTIVE_REQUIRED:token expired, no refresh token")
        client_id = str(stored.get("client_id") or "")
        if not client_id:
            raise McpError("OAUTH_INTERACTIVE_REQUIRED:cache missing client_id")
        refreshed = refresh(
            self._metadata, client_id, tokens.refresh_token, self._http, now=self._clock()
        )
        self._cache.save(client_id=client_id, tokens=refreshed, resource=self._metadata.resource)
        return refreshed.access_token

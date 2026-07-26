# Direct MCP Client — Mac-side runbook (OAuth + A/B before switch-over)

The direct MCP client removes the Claude CLI (an LLM) from the data-collection
transport: it calls the six read-only Robinhood tools directly over the MCP
protocol. The deterministic parts — protocol, parsing, option
expiration/strike selection, pagination, vault storage — are built and
unit-tested in the repo (`execution/mcp_client.py`,
`execution/robinhood_direct_collector.py`, and their tests).

Two things **cannot** be done from the cloud sandbox and must happen on the Mac,
with you present:

1. **OAuth** — obtaining a bearer token for `https://agent.robinhood.com/mcp/trading`
   requires an interactive browser authorization against your live brokerage.
2. **Live verification** — proving the client actually works against the real
   server, and that its output matches the current CLI collector.

Do **not** switch anything over until step 4 passes. The CLI collector stays the
default and the fallback.

## 0. Prerequisite

This code must be on the Mac first: merge the fork branch into upstream
`GeeLLL/ge-aitrading` via PR, then `git pull` on the Mac. (Fork changes never
reach the Mac until merged upstream.)

## 1. Get a bearer token

The client authenticates with `Authorization: Bearer <token>` for the Robinhood
MCP endpoint. You need a valid token from that server's OAuth flow. Two options:

- **Reuse the token the Claude CLI already holds** (fastest to try): the CLI
  completed the OAuth once (`/mcp` → `robinhood-trading` → browser). Inspect where
  it stored the credential (`claude mcp get robinhood-trading`, and the CLI's
  config/keychain entry) and copy the current access token. Caveat: the CLI owns
  the refresh lifecycle, so a copied token is short-lived — fine for an A/B test,
  not for permanent unattended use.
- **Stand up an independent OAuth client** (robust, for permanent use): implement
  the MCP OAuth 2.1 flow (discovery of the server's `.well-known` metadata,
  dynamic client registration + PKCE, browser redirect, token + refresh). This is
  the follow-up that makes the direct path self-sufficient; wire it as a new
  `TokenProvider` behind the seam already in `execution/mcp_client.py`.

Export it for the run scripts:

```bash
export ROBINHOOD_MCP_TOKEN="<bearer token>"
```

## 2. Smoke-test the direct collector

```bash
python3 scripts/run_direct_collector.py SPY
```

Success prints `COMPLETED transport=PYTHON_DIRECT_MCP`, a snapshot path under
`logs/raw/`, and a SHA-256. Any failure is fail-closed with a reason
(`UNAUTHORIZED` → token problem; `DIRECT_NO_EXPIRATION_IN_WINDOW` /
`DIRECT_NO_INSTRUMENTS_IN_STRIKE_BAND` → selection needs tuning against the live
chain; `TRANSPORT_FAILURE` → network/endpoint). Fix and rerun until green.

If a tool rejects an argument, the live tool schema may differ from the captured
shapes; check `claude mcp get robinhood-trading` (or the server's `tools/list`)
and adjust the argument names in `execution/robinhood_direct_collector.py`.

## 3. Confirm the read-only boundary

The direct client can only call whatever tools you invoke; it never sends order,
cancel, or transfer calls. Confirm the collector issues only the six `get_*`
tools (they are the only ones named in `collect_via_client`). No order tool is
reachable from this path.

## 4. A/B against the CLI collector (the gate)

Collect the same symbol both ways within a short window and compare:

```bash
# CLI path (existing, proven):
ROBINHOOD_SHADOW_CANARY=1 python3 scripts/launchd_shadow_worker.py   # or main.py raw-collect SPY
# Direct path:
python3 scripts/run_direct_collector.py SPY
```

Then compare the two newest envelopes under `logs/raw/<date>/`: the set of tools
called, the response `data` shapes, and the option slice (expiration + strikes)
should line up. Small differences in which exact strikes come back are fine
(quotes move); the structure and the selected expiration should match. Only once
this looks right should you consider running the direct path in parallel, and
later making it the default — keeping the CLI collector as fallback.

## What stays iron

READ_ONLY, no order tools, kill switch, no backfill — unchanged. The direct
client is a transport swap for read-only collection only; it touches none of the
order-safety path.

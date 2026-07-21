This is a read-only historical research data request.

Call only Robinhood official Trading MCP `get_equity_historicals`. Do not call
quotes, options, account, order, review, cancel, watchlist, scan, transfer, or
mutation tools. Do not browse the web.

Return raw completed regular-session 5-minute OHLCV bars for {symbol} covering
2026-06-05 through 2026-07-17. Preserve values from the tool. Do not compute
indicators, signals, rankings, returns, or recommendations. Exclude incomplete
bars and extended-hours bars. If the tool requires multiple allowed calls to
cover the date range, combine their raw bars, remove exact duplicate timestamps,
and sort ascending. Output only the required JSON schema. Do not include any
account or person identifiers.

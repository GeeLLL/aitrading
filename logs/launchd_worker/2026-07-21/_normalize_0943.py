"""Normalize pilot-20260721-0943 terminal summary to the session's key layout.

Promotes sections that pilot-20260721-0703 through -0923 keep at top level, and
moves full_universe_ranking / scheduled_symbol_detail from labels up to
evaluation, matching prior runs so CLOSE_SUMMARY can parse all of today's
terminal files uniformly.

Pure local file rewrite. No network access. Changes no recorded value.
"""

import json

P = "logs/launchd_worker/2026-07-21/pilot-20260721-0943.terminal.json"
REF = "logs/launchd_worker/2026-07-21/pilot-20260721-0923.terminal.json"

TOP_LEVEL = [
    "policy_trade", "option_quote_refresh", "simulated_fills", "trajectories",
    "mcp_tool_usage", "timing", "environment_notes", "caveats",
    "failure_reason", "dashboard_rebuild_status", "dashboard_rebuild_detail",
]

d = json.load(open(P, encoding="utf-8"))
e = d["evaluation"]

for k in TOP_LEVEL:
    if k in e:
        d[k] = e.pop(k)

lab = e["labels"]
for k in ["full_universe_ranking", "scheduled_symbol_detail"]:
    if k in lab:
        e[k] = lab.pop(k)

with open(P, "w", encoding="utf-8") as fh:
    json.dump(d, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

d2 = json.load(open(P, encoding="utf-8"))
ref = json.load(open(REF, encoding="utf-8"))
print("json_valid OK")
print("TOP:", list(d2.keys()))
print("EVAL:", list(d2["evaluation"].keys()))
print("LABELS:", list(d2["evaluation"]["labels"].keys()))
print("missing_vs_prior:", [k for k in ref if k not in d2])
print("extra_vs_prior:", [k for k in d2 if k not in ref])

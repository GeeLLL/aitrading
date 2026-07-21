"""Normalize pilot-20260721-1003 terminal summary to the session's key layout.

Moves full_universe_ranking / scheduled_symbol_detail from labels up to
evaluation, matching pilot-20260721-0703 through -0943, so CLOSE_SUMMARY can
parse all of today's terminal files uniformly. Also checks where the prior slot
keeps structural_breakout_not_in_near_miss_set and aligns it.

Pure local file rewrite. No network access. Changes no recorded value.
"""

import json

P = "logs/launchd_worker/2026-07-21/pilot-20260721-1003.terminal.json"
REF = "logs/launchd_worker/2026-07-21/pilot-20260721-0943.terminal.json"

d = json.load(open(P, encoding="utf-8"))
ref = json.load(open(REF, encoding="utf-8"))
e = d["evaluation"]
lab = e["labels"]

for k in ["full_universe_ranking", "scheduled_symbol_detail"]:
    if k in lab:
        e[k] = lab.pop(k)

# align structural_breakout_not_in_near_miss_set with wherever the prior slot keeps it
KEY = "structural_breakout_not_in_near_miss_set"
if KEY in lab and KEY in ref["evaluation"]:
    e[KEY] = lab.pop(KEY)

with open(P, "w", encoding="utf-8") as fh:
    json.dump(d, fh, indent=2, ensure_ascii=False)
    fh.write("\n")

d2 = json.load(open(P, encoding="utf-8"))
print("json_valid OK")
print("TOP:", list(d2.keys()))
print("EVAL:", list(d2["evaluation"].keys()))
print("LABELS:", list(d2["evaluation"]["labels"].keys()))
print("ref_LABELS:", list(ref["evaluation"]["labels"].keys()))
print("missing_vs_prior:", [k for k in ref if k not in d2])
print("eval_missing_vs_prior:",
      [k for k in ref["evaluation"] if k not in d2["evaluation"]])
print("eval_extra_vs_prior:",
      [k for k in d2["evaluation"] if k not in ref["evaluation"]])

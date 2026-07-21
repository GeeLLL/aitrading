"""Runner for pilot-20260721-0743 (PILOT_SAMPLE, AAPL slot).

Executes the deterministic, parameter-pinned indicator script authored for
pilot-20260721-0723 without modifying it, so both runs share identical logic,
and persists its output next to this run's other artifacts.

Read-only. No network access.
"""

import io
import json
import runpy
import sys
from contextlib import redirect_stdout

SRC = "logs/launchd_worker/2026-07-21/_compute_0723.py"
PAYLOAD = sys.argv[1]
OUT = "logs/launchd_worker/2026-07-21/_indicators_0743.json"

buf = io.StringIO()
sys.argv = [SRC, PAYLOAD]
with redirect_stdout(buf):
    runpy.run_path(SRC, run_name="__main__")

text = buf.getvalue()
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(text)

d = json.loads(text)
print("REGIME:", d["regime"])
print(json.dumps(d["regime_detail"], indent=2, sort_keys=True))
print()
rows = sorted(d["symbols"].items(), key=lambda kv: -(kv[1]["volume_ratio"] or 0))
print(f"{'SYM':6}{'volratio':>10}{'brkup':>7}{'emaup':>7}{'>vwap':>7}{'confup':>8}")
for s, v in rows:
    print(f"{s:6}{v['volume_ratio']:>10}{str(v['breakout_up']):>7}"
          f"{str(v['ema_aligned_up']):>7}{str(v['close_above_vwap']):>7}"
          f"{str(v['confirm_up']):>8}")
print()
print("newest bar begins_at:", rows[0][1]["newest_bar_begins_at"])

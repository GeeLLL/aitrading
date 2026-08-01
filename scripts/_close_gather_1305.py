"""Read-only gather for CLOSE_SUMMARY pilot-close-canary-20260727-1305. Local logs only."""
import json, os, glob

D = "2026-07-27"
out = {}

# 1. Expected schedule slots
exp = sorted(glob.glob("logs/scheduler/expected/*%s*.expected.json" % D.replace("-", "")))
out["expected"] = []
for p in exp:
    j = json.load(open(p))
    out["expected"].append({"run_id": j.get("run_id"), "scheduled_for": j.get("scheduled_for"),
                            "status": j.get("status"), "file": os.path.basename(p)})

# 2. Start ACKs
acks = sorted(glob.glob("logs/scheduler/*%s*.start.json" % D.replace("-", "")))
out["start_acks"] = [os.path.basename(p) for p in acks]

# 3. Worker receipts (summary.json + canary .json)
rec = {}
for p in sorted(glob.glob("logs/launchd_worker/%s/*.json" % D)):
    b = os.path.basename(p)
    if b.startswith("_"):
        continue
    try:
        j = json.load(open(p))
    except Exception as e:
        rec[b] = {"_parse_error": str(e)}
        continue
    if not isinstance(j, dict) or "run_id" not in j:
        continue
    rec[b] = {k: j.get(k) for k in ("run_id", "kind", "status", "result", "failure_reason",
                                    "decision", "label_decisions", "regime", "symbol",
                                    "duration_seconds", "attempts", "started_at", "ended_at",
                                    "policy_trade_executed", "evidence_class", "safety_gate")}
out["receipts"] = rec

# 4. Trajectories
traj = {}
for p in sorted(glob.glob("logs/quote_trajectories/%s/*.json" % D)):
    b = os.path.basename(p)
    key, _, ev = b[:-5].rpartition(".")
    traj.setdefault(key, []).append(ev)
out["trajectories"] = traj

# 5. Incidents
out["incidents"] = [os.path.basename(p) for p in sorted(glob.glob("logs/incidents/*.json"))]

print(json.dumps(out, indent=2, sort_keys=True))

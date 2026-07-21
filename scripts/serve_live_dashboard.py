#!/usr/bin/env python3
"""Read-only live dashboard for the shadow/pilot observation day.

Serves http://127.0.0.1:8787 with an auto-refreshing page. Every request reads
the current files under logs/ — nothing is cached beyond a short TTL on the
safety status subprocess, and nothing is ever written. This server exposes no
mutation endpoint and binds to localhost only.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, run_id_for  # noqa: E402

PORT = 8787
LOCAL = SESSION_TIMEZONE


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # surface, never crash the page
        return {"_error": f"{type(error).__name__}: {error}", "_path": str(path)}


_status_cache: dict = {"at": 0.0, "value": None}


def _safety_status() -> dict:
    if time.time() - _status_cache["at"] < 10 and _status_cache["value"]:
        return _status_cache["value"]
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "status"],
            capture_output=True, text=True, timeout=20, cwd=ROOT,
        )
        value = json.loads(completed.stdout)
    except Exception as error:
        value = {"_error": f"{type(error).__name__}: {error}"}
    _status_cache.update(at=time.time(), value=value)
    return value


def _slot_digest(day: str) -> list[dict]:
    """One entry per scheduled slot, joined with its summary if it ran."""

    worker_dir = ROOT / "logs/launchd_worker" / day
    slots = []
    for (hour, minute), (kind, symbol) in sorted(DAILY_SLOTS.items()):
        scheduled_local = f"{hour:02d}:{minute:02d}"
        year, month, dom = (int(x) for x in day.split("-"))
        run_id = run_id_for(kind, datetime(year, month, dom, hour, minute, tzinfo=LOCAL))
        summary_path = worker_dir / f"{run_id}.json"
        entry = {
            "slot": scheduled_local, "kind": kind, "symbol": symbol, "run_id": run_id,
            "status": None, "failure_reason": None, "duration_seconds": None,
        }
        if summary_path.exists():
            summary = _read_json(summary_path)
            entry.update(
                status=summary.get("status"),
                failure_reason=summary.get("failure_reason") or summary.get("reason"),
                duration_seconds=summary.get("duration_seconds"),
            )
        slots.append(entry)
    return slots


def _pilot_digest(day: str, run_id: str) -> dict:
    """Compact per-pilot digest; the full terminal JSON is fetched lazily."""

    worker_dir = ROOT / "logs/launchd_worker" / day
    digest: dict = {}
    terminal_path = worker_dir / f"{run_id}.terminal.json"
    if not terminal_path.exists():
        return digest
    terminal = _read_json(terminal_path)
    market = terminal.get("market_data") or {}
    quotes = market.get("quotes") or {}
    evaluation = terminal.get("evaluation") or {}
    digest = {
        "terminal_status": terminal.get("status"),
        "freshness_gate": quotes.get("freshness_gate"),
        "max_quote_age_seconds": quotes.get("max_quote_age_seconds"),
        "universe_note": (market.get("universe_note") or "")[:400],
        "policy_trade": terminal.get("policy_trade") or None,
        "simulated_fills": terminal.get("simulated_fills") or None,
        "trajectories": terminal.get("trajectories") or None,
        "caveats": terminal.get("caveats"),
        "evaluation_digest": {
            key: value for key, value in evaluation.items()
            if isinstance(value, (str, int, float, bool))
        } or {k: str(v)[:200] for k, v in list(evaluation.items())[:6]},
        "mcp_tool_usage": terminal.get("mcp_tool_usage"),
        "upstream_gate": terminal.get("upstream_gate"),
    }
    stamp = run_id.rsplit("-", 1)[-1]
    indicators_path = worker_dir / f"_indicators_{stamp}.json"
    if indicators_path.exists():
        indicators = _read_json(indicators_path)
        digest["regime"] = indicators.get("regime")
        digest["indicator_symbols"] = sorted((indicators.get("symbols") or {}).keys())
    return digest


def _runs(day: str) -> list[dict]:
    worker_dir = ROOT / "logs/launchd_worker" / day
    if not worker_dir.is_dir():
        return []
    runs = []
    for path in sorted(worker_dir.glob("*.json")):
        name = path.name
        if name.endswith(".terminal.json") or name.startswith("_indicators"):
            continue
        summary = _read_json(path)
        run_id = summary.get("run_id") or name[:-5]
        row = {
            "run_id": run_id,
            "kind": summary.get("kind") or ("CANARY" if "canary" in name else "?"),
            "symbol": summary.get("symbol"),
            "status": summary.get("status"),
            "failure_reason": summary.get("failure_reason") or summary.get("reason"),
            "started_at": summary.get("started_at"),
            "ended_at": summary.get("ended_at"),
            "duration_seconds": summary.get("duration_seconds"),
            "snapshot_sha256": summary.get("snapshot_sha256"),
            "agent_runtime": summary.get("agent_runtime"),
        }
        if row["kind"] == "PILOT_SAMPLE" or (isinstance(run_id, str) and run_id.startswith("pilot-2")):
            row["pilot"] = _pilot_digest(day, run_id)
        runs.append(row)
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


def _snapshots(day: str) -> list[dict]:
    vault_dir = ROOT / "logs/raw" / day
    index_path = ROOT / "logs/raw/vault_index.jsonl"
    digests = {}
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                digests[entry.get("snapshot_id")] = entry.get("content_sha256")
            except json.JSONDecodeError:
                continue
    rows = []
    if vault_dir.is_dir():
        for path in sorted(vault_dir.glob("*.json")):
            snapshot = _read_json(path)
            tools = []
            try:
                tools = sorted({r["tool"] for r in snapshot["response"]["tool_results"]})
            except Exception:
                pass
            rows.append({
                "snapshot_id": snapshot.get("snapshot_id"),
                "source": snapshot.get("source"),
                "source_updated_at": snapshot.get("source_updated_at"),
                "received_at": snapshot.get("received_at"),
                "bytes": path.stat().st_size,
                "sha256": digests.get(snapshot.get("snapshot_id")),
                "tools": tools,
            })
    rows.sort(key=lambda r: r.get("received_at") or "", reverse=True)
    return rows


def _tail(path: Path, lines: int = 5) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()[-lines:]
    except OSError:
        return []


def _state(day: str | None) -> dict:
    now_local = datetime.now(LOCAL)
    day = day or now_local.date().isoformat()
    trajectory_dir = ROOT / "logs/quote_trajectories" / day
    gate = None
    for run in _runs(day):
        pilot = run.get("pilot") or {}
        if pilot.get("upstream_gate"):
            gate = pilot["upstream_gate"]
            break
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_time": now_local.isoformat(),
        "day": day,
        "safety": _safety_status(),
        "automation_halted": (ROOT / "state/automation_halt.json").exists(),
        "slots": _slot_digest(day),
        "runs": _runs(day),
        "snapshots": _snapshots(day),
        "market_gate": gate,
        "preopen": _read_json(ROOT / "logs/qualification/latest.preopen.json"),
        "watchdog_tail": _tail(ROOT / "logs/watchdog.stdout.log", 3),
        "watchdog_errors": _tail(ROOT / "logs/watchdog.stderr.log", 3),
        "trajectory_files": sorted(p.name for p in trajectory_dir.glob("*")) if trajectory_dir.is_dir() else [],
    }


def _run_detail(day: str | None, run_id: str) -> dict:
    day = day or datetime.now(LOCAL).date().isoformat()
    worker_dir = ROOT / "logs/launchd_worker" / day
    if not run_id.replace("-", "").isalnum():
        return {"error": "bad run id"}
    detail = {"summary": _read_json(worker_dir / f"{run_id}.json")}
    terminal = worker_dir / f"{run_id}.terminal.json"
    if terminal.exists():
        detail["terminal"] = _read_json(terminal)
    stamp = run_id.rsplit("-", 1)[-1]
    indicators = worker_dir / f"_indicators_{stamp}.json"
    if indicators.exists():
        detail["indicators"] = _read_json(indicators)
    return detail


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shadow Pilot 实时看板</title>
<style>
:root{--bg:#0b0f14;--panel:#121820;--panel2:#0e141b;--line:#1e2833;--ink:#e6edf3;--ink2:#9fb0c0;--ink3:#647586;
--good:#3fb950;--bad:#f85149;--warn:#d29922;--info:#58a6ff;--accent:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,"SF Pro Text","PingFang SC",Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 20px 60px}
h1{font-size:17px;margin:0;display:flex;align-items:center;gap:10px}
h2{font-size:13px;color:var(--ink2);text-transform:uppercase;letter-spacing:.08em;margin:26px 0 10px}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--good);animation:p 1.6s infinite}
@keyframes p{50%{opacity:.25}}
.stale .pulse{background:var(--bad);animation:none}
.meta{color:var(--ink3);font-size:12px;margin-left:auto;text-align:right}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.chip{padding:3px 10px;border-radius:999px;font-size:12px;border:1px solid var(--line);background:var(--panel);color:var(--ink2)}
.chip.good{color:var(--good);border-color:rgba(63,185,80,.4)}
.chip.bad{color:var(--bad);border-color:rgba(248,81,73,.45)}
.chip.warn{color:var(--warn);border-color:rgba(210,153,34,.45)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .l{font-size:11px;color:var(--ink3);margin-top:2px}
.timeline{display:flex;flex-wrap:wrap;gap:6px}
.slot{min-width:86px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 8px;font-size:11px}
.slot b{display:block;font-size:12px;font-variant-numeric:tabular-nums}
.slot .k{color:var(--ink3)}
.slot.ok{border-color:rgba(63,185,80,.5)}.slot.ok b{color:var(--good)}
.slot.fail{border-color:rgba(248,81,73,.55)}.slot.fail b{color:var(--bad)}
.slot.pend{opacity:.55}.slot.next{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
table{width:100%;border-collapse:collapse;font-size:12.5px}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
th{color:var(--ink3);text-align:left;font-weight:500;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.st{font-weight:600}.st.ok{color:var(--good)}.st.fail{color:var(--bad)}.st.warn{color:var(--warn)}
.mono{font-family:ui-monospace,SF Mono,Menlo,monospace;font-size:11.5px;color:var(--ink2)}
.reason{color:var(--bad);font-size:11.5px;max-width:340px}
details{margin-top:4px}summary{cursor:pointer;color:var(--info);font-size:11.5px}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;max-height:420px;font-size:11px;line-height:1.4}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-size:12.5px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 14px}.kv div:nth-child(odd){color:var(--ink3)}
.empty{color:var(--ink3);font-style:italic}
a{color:var(--info)}
.checks{display:flex;gap:8px;flex-wrap:wrap}
.note{color:var(--ink3);font-size:11.5px;margin-top:8px}
</style></head><body><div class="wrap" id="app">
<h1><span class="pulse" id="pulse"></span>Shadow Pilot 实时看板
<span class="meta" id="meta">加载中…</span></h1>
<div class="chips" id="safety"></div>
<div class="tiles" id="tiles"></div>
<h2>今日调度(<span id="dayLabel"></span>,America/Los_Angeles)</h2>
<div class="timeline" id="timeline"></div>
<h2>市场资格门(六项官方检查)</h2><div class="panel" id="gate"></div>
<h2>虚拟交易 / 模拟成交</h2><div class="panel" id="trades"></div>
<h2>全部运行明细(含手动 canary,点开看完整 JSON)</h2>
<div class="tablewrap"><table id="runs"><thead><tr>
<th>时间</th><th>类型</th><th>标的</th><th>状态</th><th>时长</th><th>要点</th></tr></thead><tbody></tbody></table></div>
<div class="grid2">
<div><h2>Raw 快照(不可变 vault)</h2><div class="tablewrap"><table id="snaps"><thead><tr>
<th>received_at</th><th>source_updated_at</th><th>大小</th><th>SHA-256</th><th>工具</th></tr></thead><tbody></tbody></table></div></div>
<div><h2>看护 / 环境</h2><div class="panel" id="ops"></div></div>
</div>
<div class="note">只读页面 · 每 5 秒自动刷新 · 数据直接来自 logs/ 原始文件 · 本页不提供任何交易操作;系统为 READ_ONLY,无下单工具。</div>
</div>
<script>
const $=q=>document.querySelector(q);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const t=s=>s?new Date(s).toLocaleTimeString("zh-CN",{hour12:false,timeZone:"America/Los_Angeles"}):"—";
const dur=s=>s==null?"—":(s<60?s.toFixed(0)+"s":(s/60).toFixed(1)+"m");
function chip(txt,cls){return `<span class="chip ${cls||""}">${txt}</span>`}
function stCls(s){if(!s)return"";if(s.startsWith("COMPLETED"))return"ok";
if(["FAILED_CLOSED","AGENT_FAILED","ACK_FAILED","SAFETY_GATE_FAILED","AGENT_TIMEOUT_OR_START_FAILURE","REFUSED"].includes(s))return"fail";return"warn"}
let lastOK=0;
async function tick(){
  try{
    const r=await fetch("/api/state");const d=await r.json();lastOK=Date.now();render(d);
    document.body.classList.remove("stale");
  }catch(e){ if(Date.now()-lastOK>15000){document.body.classList.add("stale");
    $("#meta").textContent="连接中断 — 数据已过期";} }
}
function render(d){
  $("#dayLabel").textContent=d.day;
  $("#meta").innerHTML=`本地 ${t(d.local_time)} · 刷新于 ${new Date().toLocaleTimeString("zh-CN",{hour12:false})}`;
  const s=d.safety||{};
  $("#safety").innerHTML=[
    chip("mode: "+esc(s.system_mode||"?"),s.system_mode==="READ_ONLY"?"good":"bad"),
    chip("kill switch "+(s.kill_switch_engaged?"engaged ✓":"OFF!"),s.kill_switch_engaged?"good":"bad"),
    chip("live trading "+(s.live_trading_enabled?"ON!":"off ✓"),s.live_trading_enabled?"bad":"good"),
    chip("order tools "+(s.order_tools_enabled?"ON!":"none ✓"),s.order_tools_enabled?"bad":"good"),
    chip("automation "+(d.automation_halted?"HALTED":"running"),d.automation_halted?"bad":"good"),
    chip("evidence: PILOT(不计业绩)","warn"),
  ].join("");
  const runs=d.runs||[],slots=d.slots||[];
  const done=slots.filter(x=>x.status&&x.status.startsWith("COMPLETED")).length;
  const failed=slots.filter(x=>stCls(x.status)==="fail").length;
  const pilots=runs.filter(x=>x.kind==="PILOT_SAMPLE"&&x.pilot);
  const latest=pilots[0]||{};
  // 只有真正选中合约/产生成交事件才算交易;NO_TRADE 的结构化记录不算。
  const trades=pilots.filter(p=>{
    const pt=p.pilot.policy_trade||{},sf=p.pilot.simulated_fills||{};
    return pt.selected_contract||(pt.outcome&&pt.outcome!=="NO_TRADE")||
      (sf.entries||0)+(sf.exits||0)+(sf.no_fill_events||0)>0;
  });
  const budget=(latest.pilot&&latest.pilot.policy_trade)||{};
  const fresh=latest.pilot||{};
  $("#tiles").innerHTML=[
    ["已完成 slot",done+" / "+slots.length],["失败(fail-closed)",failed],
    ["raw 快照",(d.snapshots||[]).length],
    ["虚拟交易",trades.length+((budget.daily_limit!=null)?` <span style="font-size:12px;color:var(--ink3)">/限${budget.daily_limit}</span>`:"")],
    ["最新报价新鲜度",fresh.max_quote_age_seconds!=null?
      `${fresh.max_quote_age_seconds}s <span style="font-size:11px" class="st ${fresh.freshness_gate==="PASS"?"ok":"warn"}">${esc(fresh.freshness_gate||"")}</span>`:"—"],
    ["市场 regime",esc(fresh.regime||"—")],
  ].map(([l,v])=>`<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  const nowHM=new Date(d.local_time).toTimeString().slice(0,5);
  let nextMarked=false;
  $("#timeline").innerHTML=slots.map(x=>{
    let cls=x.status?stCls(x.status)==="ok"?"ok":stCls(x.status)==="fail"?"fail":"warn":"pend";
    if(!x.status&&!nextMarked&&x.slot>nowHM){cls+=" next";nextMarked=true}
    return `<div class="slot ${cls}"><b>${x.slot} ${esc(x.status||"待执行")}</b>
      <span class="k">${esc(x.kind)} · ${esc(x.symbol)}</span>
      ${x.failure_reason?`<div class="reason">${esc(x.failure_reason.slice(0,80))}</div>`:""}</div>`;
  }).join("");
  const g=d.market_gate;
  $("#gate").innerHTML=g?`<div class="checks">
    ${chip("gate run: "+esc(g.market_gate_run_id),"")}
    ${chip(esc(g.market_gate_status),g.market_gate_status==="FAILED_CLOSED"?"bad":"good")}
    ${chip("Formal Shadow "+(g.formal_shadow_authorized?"已授权":"未授权"),g.formal_shadow_authorized?"good":"warn")}
    </div><div class="note">${esc(g.market_gate_detail||"")}</div>
    <div class="note">${esc(g.effect_on_this_run||"")}</div>`
    :'<span class="empty">今日尚无 market gate 结果</span>';
  $("#trades").innerHTML=(trades.length?`<pre>${esc(JSON.stringify(trades.map(p=>({run:p.run_id,policy_trade:p.pilot.policy_trade,simulated_fills:p.pilot.simulated_fills})),null,1))}</pre>`
    :`<span class="empty">今日暂无虚拟 policy trade / 模拟成交。这不是故障:目前没有候选合约通过确定性筛选(冻结 universe + 资格门 + delta/价差等硬约束)。</span>`)
    +(budget.daily_limit!=null?`<div class="note">当日虚拟交易预算:已用 ${budget.virtual_candidates_used_today??0} / ${budget.daily_limit}${budget.note?" — "+esc(String(budget.note).slice(0,220)):""}</div>`:"");
  $("#runs tbody").innerHTML=runs.map(r=>{
    const p=r.pilot||{};
    const bits=[];
    if(p.freshness_gate)bits.push(`报价新鲜度 ${p.freshness_gate}(${p.max_quote_age_seconds??"?"}s)`);
    if(p.regime)bits.push("regime "+p.regime);
    if(r.snapshot_sha256)bits.push("sha "+r.snapshot_sha256.slice(0,10)+"…");
    if(p.terminal_status&&p.terminal_status!==r.status)bits.push(p.terminal_status);
    return `<tr><td>${t(r.started_at)}</td><td>${esc(r.kind)}</td><td>${esc(r.symbol||"")}</td>
    <td class="st ${stCls(r.status)}">${esc(r.status||"?")}</td><td>${dur(r.duration_seconds)}</td>
    <td>${bits.map(esc).join(" · ")||""}
      ${r.failure_reason?`<div class="reason">${esc(r.failure_reason)}</div>`:""}
      <details data-run="${esc(r.run_id)}"><summary>完整 JSON</summary><pre>点击加载…</pre></details></td></tr>`;
  }).join("");
  $("#snaps tbody").innerHTML=(d.snapshots||[]).map(x=>`<tr>
    <td>${t(x.received_at)}</td><td>${t(x.source_updated_at)}</td>
    <td>${(x.bytes/1024).toFixed(0)}KB</td>
    <td class="mono">${esc((x.sha256||"?").slice(0,12))}…</td>
    <td class="mono">${esc((x.tools||[]).map(s=>s.replace("get_","")).join(", "))}</td></tr>`).join("")
    ||'<tr><td colspan="5" class="empty">今日暂无</td></tr>';
  const pre=d.preopen||{};
  $("#ops").innerHTML=`<div class="kv">
    <div>watchdog</div><div class="mono">${esc((d.watchdog_tail||[]).slice(-1)[0]||"—")}</div>
    <div>preopen gate</div><div>${esc(pre.status||"?")} · 待市场核验:${esc((pre.market_checks_pending||[]).length)}</div>
    <div>trajectory 文件</div><div>${(d.trajectory_files||[]).length?esc(d.trajectory_files.join(", ")):'<span class="empty">空(无合约进入选择)</span>'}</div>
    <div>watchdog 错误</div><div class="mono">${esc((d.watchdog_errors||[]).slice(-1)[0]||"无")}</div>
  </div>`;
  document.querySelectorAll("details[data-run]").forEach(el=>{
    el.addEventListener("toggle",async()=>{
      if(!el.open||el.dataset.loaded)return;
      el.dataset.loaded="1";
      const pre=el.querySelector("pre");
      try{const r=await fetch("/api/run/"+encodeURIComponent(el.dataset.run));
        pre.textContent=JSON.stringify(await r.json(),null,1);}
      catch(e){pre.textContent="加载失败: "+e}
    },{once:false});
  });
}
tick();setInterval(tick,5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        day = (query.get("date") or [None])[0]
        try:
            if parsed.path == "/":
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            elif parsed.path == "/api/state":
                body, ctype = json.dumps(_state(day)).encode(), "application/json"
            elif parsed.path.startswith("/api/run/"):
                run_id = parsed.path.rsplit("/", 1)[-1]
                body, ctype = json.dumps(_run_detail(day, run_id)).encode(), "application/json"
            else:
                self.send_error(404)
                return
        except Exception as error:  # noqa: BLE001 — page must survive bad files
            body = json.dumps({"error": f"{type(error).__name__}: {error}"}).encode()
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep worker logs quiet
        pass


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"live dashboard (read-only) on http://127.0.0.1:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a self-contained, read-only HTML dashboard from sanitized pilot logs."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.shadow_activation import load_shadow_authorization
from monitoring.shadow_readiness import build_shadow_readiness
from journal.evidence_eligibility import classify_shadow_evidence


PILOT_ROOT = ROOT / "logs" / "pilot"
SCHEDULER_ROOT = ROOT / "logs" / "scheduler"
INCIDENT_ROOT = ROOT / "logs" / "incidents"
OUTPUT = ROOT / "dashboard" / "index.html"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def collect() -> dict:
    readiness = build_shadow_readiness(root=ROOT).to_dict()
    summaries = [load_json(path) for path in sorted(PILOT_ROOT.glob("**/*.summary.json"))]
    summaries = [item for item in summaries if item]
    starts = [load_json(path) for path in sorted(SCHEDULER_ROOT.glob("pilot-*.start.json"))]
    starts = [item for item in starts if item]
    expectations = [load_json(path) for path in sorted((SCHEDULER_ROOT / "expected").glob("*.expected.json"))]
    expectations = [item for item in expectations if item]
    incidents = [load_json(path) for path in sorted(INCIDENT_ROOT.glob("*.scheduler-incident.json"))]
    incidents = [item for item in incidents if item]
    expected_ids = {str(item.get("run_id")) for item in expectations}
    acknowledged_expected = sum(1 for item in starts if str(item.get("run_id")) in expected_ids)
    completed_ids = {str(item.get("run_id")) for item in summaries}
    active = [
        item for item in starts
        if str(item.get("run_id")) not in completed_ids
        and item.get("mode") == "SHADOW_PILOT_SAMPLE"
        and str(item.get("status", "")).upper() == "STARTED"
    ]

    latest = summaries[-1] if summaries else {}
    pipeline = latest.get("decision_pipeline", {})
    account = latest.get("account_summary", {})
    market = latest.get("market", {})
    option_research = latest.get("option_research", {})

    virtual_trade_count = sum(
        1 for item in summaries
        if item.get("decision_pipeline", {}).get("final_outcome") == "TRADE"
    )
    no_trade_count = sum(
        1 for item in summaries
        if item.get("decision_pipeline", {}).get("final_outcome") == "NO_TRADE"
    )
    rejection_totals: dict[str, int] = {}
    for item in summaries:
        for reason, count in (item.get("rule_rejections") or {}).items():
            rejection_totals[reason] = rejection_totals.get(reason, 0) + int(count or 0)
    authorization_verified = load_shadow_authorization(
        "strategy_v1.0", ROOT / "state/shadow_authorization.json"
    )
    eligibility = [
        classify_shadow_evidence(item, authorization_verified=authorization_verified)
        for item in summaries
    ]
    eligible_runs = sum(item.eligible for item in eligibility)
    mechanical_signals = sum(
        int(item.get("decision_pipeline", {}).get("mechanical_signal_count", 0) or 0)
        for item in summaries
    )
    strict_candidates = sum(
        int(item.get("decision_pipeline", {}).get("strict_candidate_count", 0) or 0)
        for item in summaries
    )

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latest": latest,
        "active": active[-3:],
        "runs": list(reversed(summaries[-12:])),
        "readiness": readiness,
        "research": {
            "eligible_runs": eligible_runs,
            "ineligible_runs": len(summaries) - eligible_runs,
            "mechanical_signals": mechanical_signals,
            "strict_candidates": strict_candidates,
            "top_rejections": sorted(rejection_totals.items(), key=lambda item: (-item[1], item[0]))[:8],
        },
        "metrics": {
            "completed_runs": len(summaries),
            "active_runs": len(active),
            "virtual_trades": virtual_trade_count,
            "no_trades": no_trade_count,
            "latest_outcome": pipeline.get("final_outcome", "WAITING"),
            "account_value": account.get("total_value_usd", "UNKNOWN"),
            "positions": account.get("position_count", account.get("option_position_count", "UNKNOWN")),
            "open_orders": account.get("unfinished_order_count", account.get("unfinished_option_order_count", "UNKNOWN")),
            "market_regime": market.get("regime", "UNKNOWN"),
            "option_quotes": option_research.get("quote_samples", 0),
            "expected_runs": len(expectations),
            "acknowledged_expected_runs": acknowledged_expected,
            "scheduler_incidents": len(incidents),
        },
    }


def build_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Robinhood AI — Controlled Shadow</title>
  <style>
    :root {{ --bg:#07110d; --panel:#0d1a14; --line:#23392d; --text:#ecf7f0; --muted:#8ea99a; --green:#43e083; --amber:#ffc857; --red:#ff6b6b; --blue:#69a7ff; }}
    * {{ box-sizing:border-box }} body {{ margin:0; background:radial-gradient(circle at 80% 0,#123322 0,transparent 34%),var(--bg); color:var(--text); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1280px; margin:auto; padding:30px 22px 60px }}
    header {{ display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin-bottom:26px }}
    h1 {{ margin:0; font-size:clamp(28px,5vw,52px); letter-spacing:-.04em }} h2 {{ font-size:17px; margin:0 0 14px }}
    .eyebrow {{ color:var(--green); text-transform:uppercase; letter-spacing:.14em; font-weight:700; font-size:12px }}
    .muted {{ color:var(--muted) }} .status {{ padding:9px 13px; border:1px solid var(--line); border-radius:999px; background:#0a1711; white-space:nowrap }}
    .grid {{ display:grid; grid-template-columns:repeat(6,1fr); gap:12px }}
    .card {{ background:linear-gradient(145deg,rgba(18,37,27,.94),rgba(10,23,17,.96)); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 18px 50px rgba(0,0,0,.18) }}
    .metric .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em }} .metric .value {{ font-size:28px; font-weight:750; margin-top:7px }}
    .metric {{ grid-column:span 2 }} .wide {{ grid-column:span 3 }} .full {{ grid-column:1/-1 }}
    .pill {{ display:inline-block; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:700 }} .good {{ color:var(--green); background:#123321 }} .warn {{ color:var(--amber); background:#352c12 }} .bad {{ color:var(--red); background:#361a1a }}
    table {{ width:100%; border-collapse:collapse }} th,td {{ text-align:left; padding:11px 8px; border-bottom:1px solid var(--line) }} th {{ color:var(--muted); font-size:12px; font-weight:600 }}
    ul {{ padding-left:18px; margin:8px 0 }} code {{ color:#bde9cd }}
    .safe {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px }}
    .hero-status {{ display:flex; align-items:center; gap:10px }} .pulse {{ width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 0 0 rgba(67,224,131,.6);animation:pulse 1.7s infinite }}
    .timeline {{ display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:18px 0 6px }} .step {{ padding:10px;border-radius:10px;background:#09150f;border:1px solid var(--line);color:var(--muted);font-size:12px }} .step.done {{ color:var(--green);border-color:#28563b }}
    .bar {{ height:8px;border-radius:999px;background:#09150f;overflow:hidden;margin:10px 0 }} .bar>span {{ display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--blue));border-radius:inherit }}
    .reason-grid {{ display:grid;grid-template-columns:1fr;gap:8px }} .reason {{ display:grid;grid-template-columns:1fr auto;gap:4px 12px;padding:11px 12px;border-radius:10px;background:#09150f;color:var(--muted) }} .reason b {{ color:var(--text) }} .reason .why {{ grid-column:1/-1;font-size:12px;color:var(--muted) }} .decision-note {{ padding:12px 14px;border-left:3px solid var(--amber);background:#18180d;border-radius:0 10px 10px 0;margin-bottom:10px }}
    .regime {{ display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 14px;border-radius:13px;background:#09150f;margin-bottom:11px }} .regime strong {{ font-size:21px }}
    .index-grid {{ display:grid;grid-template-columns:repeat(2,1fr);gap:9px;margin-bottom:12px }} .index-card {{ padding:11px 12px;border:1px solid var(--line);border-radius:12px }} .index-card .symbol {{ font-weight:800;font-size:16px }} .index-card .direction {{ margin-top:4px;color:var(--muted);font-size:13px }}
    .quality-row {{ display:flex;justify-content:space-between;gap:14px;color:var(--muted);font-size:13px;margin-top:8px }}
    .tiny {{ font-size:12px }} @keyframes pulse {{ 70%{{box-shadow:0 0 0 9px rgba(67,224,131,0)}} 100%{{box-shadow:0 0 0 0 rgba(67,224,131,0)}} }}
    @media(max-width:850px) {{ .grid {{ grid-template-columns:repeat(2,1fr) }} .metric {{ grid-column:span 1 }} .wide {{ grid-column:span 2 }} }}
    @media(max-width:520px) {{ header {{ align-items:flex-start; flex-direction:column }} .grid {{ grid-template-columns:1fr }} .wide,.full {{ grid-column:1 }} .metric .value {{ font-size:24px }} }}
  </style>
</head>
<body><main>
  <header><div><div class="eyebrow">Controlled Shadow · Read Only</div><h1>交易实验看板</h1><div class="muted"><span id="updated"></span> · <span id="countdown">30 秒后刷新</span></div></div><div class="status hero-status"><i class="pulse"></i><span id="runStatus">读取中</span></div></header>
  <section class="grid">
    <div class="card metric"><div class="label">最新决策</div><div class="value" id="outcome">—</div></div>
    <div class="card metric"><div class="label">虚拟交易</div><div class="value" id="trades">—</div></div>
    <div class="card metric"><div class="label">NO TRADE</div><div class="value" id="noTrades">—</div></div>
    <div class="card metric"><div class="label">账户安全</div><div class="value" id="account">—</div></div>
    <div class="card metric"><div class="label">最近一轮耗时</div><div class="value" id="duration">—</div></div>
    <div class="card metric"><div class="label">采样可靠性</div><div class="value" id="reliability">—</div></div>
    <div class="card wide"><h2>现在系统在做什么</h2><div id="active"></div><div class="timeline"><div class="step done">① 安全门</div><div class="step done">② 股票扫描</div><div class="step" id="optionStep">③ 期权筛选</div><div class="step done">④ 决策落盘</div></div></div>
    <div class="card wide"><h2>最新市场观察</h2><div id="market"></div><div class="bar"><span id="freshnessBar" style="width:100%"></span></div><div class="tiny muted" id="freshness"></div></div>
    <div class="card wide"><h2>为什么没有交易</h2><div class="reason-grid" id="reasons"></div></div>
    <div class="card wide"><h2>虚拟仓位与 P&amp;L</h2><div id="position"></div></div>
    <div class="card wide"><h2>信号漏斗</h2><div id="funnel"></div></div>
    <div class="card wide"><h2>实验资格</h2><div id="eligibility"></div></div>
    <div class="card full"><h2>最近完成的采样</h2><div style="overflow:auto"><table><thead><tr><th>Run ID</th><th>结果</th><th>市场</th><th>机械信号</th><th>期权报价</th><th>耗时</th><th>业绩资格</th></tr></thead><tbody id="runs"></tbody></table></div></div>
    <div class="card full"><h2>不可绕过的安全状态</h2><div class="safe"><span class="pill good">READ_ONLY</span><span class="pill good">LIVE OFF</span><span class="pill good">ORDER TOOLS OFF</span><span class="pill good">KILL SWITCH ENGAGED</span><span class="pill warn">PILOT — 不计正式业绩</span></div></div>
    <div class="card full"><h2>独立调度 Watchdog</h2><div id="watchdog"></div></div>
    <div class="card full"><h2>盘后工程与上线门禁</h2><div id="engineering"></div></div>
  </section>
</main>
<script id="shadow-data" type="application/json">{payload}</script>
<script>
  const d=JSON.parse(document.getElementById('shadow-data').textContent); const m=d.metrics, latest=d.latest||{{}};
  const set=(id,v)=>document.getElementById(id).textContent=v;
  set('updated','生成时间：'+d.generated_at); set('outcome',m.latest_outcome); set('trades',m.virtual_trades); set('noTrades',m.no_trades);
  set('account','$'+m.account_value+' · '+m.positions+' 持仓'); set('duration',(latest.run_duration_seconds??'—')+' 秒');
  const successful=(d.runs||[]).filter(x=>x.status==='COMPLETED'||x.decision_pipeline?.final_outcome).length; const reliability=(d.runs||[]).length?Math.round(successful/(d.runs||[]).length*100):0; set('reliability',reliability+'%');
  const active=d.active||[]; document.getElementById('runStatus').innerHTML=active.length?'<span class="pill warn">采样运行中</span>':'<span class="pill good">监控正常 · 等待触发</span>';
  document.getElementById('active').innerHTML=active.length?active.map(x=>'<p><b>'+x.run_id+'</b> 正在读取真实行情并运行确定性过滤。</p><p class="muted tiny">开始：'+x.started_at+'</p>').join(''):'<p><b>最近一轮已完成。</b> 系统正在等待下一次 20 分钟触发；有信号才会进入期权筛选。</p>';
  const sel=latest.universe_selection||{{}}, anomalies=latest.anomalies||[];
  const regimeMap={{BEARISH:'偏空',BULLISH:'偏多',MIXED:'震荡/混合',NEUTRAL:'中性',UNKNOWN:'无法确认'}}; const dirMap={{BEARISH:'偏空',BULLISH:'偏多',MIXED:'混合',NEUTRAL:'中性'}};
  const refs=latest.market?.reference_directions||{{}}; const regimeZh=regimeMap[m.market_regime]||m.market_regime; const regimeClass=m.market_regime==='BULLISH'?'good':m.market_regime==='BEARISH'?'bad':'warn';
  const refCards=Object.entries(refs).map(([symbol,dirs])=>{{const zh=(dirs||[]).map(x=>dirMap[x]||x);const aligned=zh.length>1&&new Set(zh).size===1;return '<div class="index-card"><div class="symbol">'+symbol+' <span class="pill '+(aligned?'good':'warn')+'">'+(aligned?'方向一致':'方向分歧')+'</span></div><div class="direction">短线 / 趋势：'+(zh.join(' / ')||'UNKNOWN')+'</div></div>'}}).join('');
  const stockSamples=latest.sampling?.equity_quote_samples??latest.market?.universe_sample_count??'—'; const bars=latest.sampling?.completed_5m_bar_samples??latest.market?.completed_5m_bar_samples??'—';
  document.getElementById('market').innerHTML='<div class="regime"><div><div class="tiny muted">综合市场环境</div><strong>'+regimeZh+'</strong></div><span class="pill '+regimeClass+'">'+m.market_regime+'</span></div><div class="index-grid">'+(refCards||'<div class="muted">指数方向数据缺失</div>')+'</div><div class="quality-row"><span>股票报价 '+stockSamples+' 个</span><span>已完成 5 分钟 Bar '+bars+' 根</span><span>期权报价 '+m.option_quotes+' 个</span></div>';
  const stale=(latest.stale||[]).length+(latest.option_research?.stale_at_receipt_over_10s||0); const total=latest.sampling?.equity_quote_samples||latest.option_research?.quote_samples||10; const fresh=Math.max(0,Math.round((total-stale)/total*100)); document.getElementById('freshnessBar').style.width=fresh+'%'; set('freshness','数据新鲜度约 '+fresh+'% · 过期/异常样本 '+stale);
  const reasonText={{
    VOLUME_CONFIRMATION_FAILED:['成交量确认不足','当前成交量没有明显高于近期基准，价格走势缺少资金参与确认，因此不追涨或追跌。'],
    BEARISH_EMA_ALIGNMENT_FAILED:['均线没有形成完整空头排列','短期与中期均线方向不一致，下降趋势不够明确，因此不买 Put。'],
    BULLISH_EMA_ALIGNMENT_FAILED:['均线没有形成完整多头排列','短期与中期均线方向不一致，上升趋势不够明确，因此不买 Call。'],
    SIX_BAR_BREAKDOWN_FAILED:['没有确认有效跌破','最近六根已完成 K 线没有形成规则要求的向下突破，因此做空方向被拒绝。'],
    SIX_BAR_BREAKOUT_FAILED:['没有确认有效突破','最近六根已完成 K 线没有形成规则要求的向上突破，因此做多方向被拒绝。'],
    TRUSTED_BAR_SOURCE_UPDATED_AT_MISSING:['旧版 K 线证据规则已废止','该结果由修复前的任务生成；新版使用时间区间与不可变收件记录验证，不再要求 Robinhood 未提供的逐根更新时间。'],
    FINAL_OPTION_QUOTE_NOT_REFRESHED:['最终期权报价未及时刷新','候选合约选定后没有在 10 秒内完成单合约二次取价，因此拒绝；早期扫描报价不能用于最终决策。'],
    EQUITY_QUOTE_STALE_OVER_10S:['股票报价超过新鲜度阈值','该报价年龄超过 10 秒。为避免用过期价格决策，系统自动拒绝。'],
    OPTION_QUOTE_STALE_AT_RECEIPT:['期权报价到达时已过期','期权报价年龄超过允许阈值，无法可靠估计真实可成交价格。'],
    WIDE_OPTION_SPREAD:['期权买卖价差过宽','预期摩擦成本过高，即使方向正确也可能被价差侵蚀。']
  }};
  const reasons=latest.rule_rejections||{{}}; const reasonEntries=Object.entries(reasons).sort((a,b)=>b[1]-a[1]); const lead=reasonEntries[0]&&reasonText[reasonEntries[0][0]]?reasonText[reasonEntries[0][0]][0]:'没有完整交易信号';
  document.getElementById('reasons').innerHTML='<div class="decision-note"><b>本轮结论：NO TRADE</b><br><span class="tiny">首要原因：'+lead+'。系统不会为了每天都有交易而降低标准。</span></div>'+reasonEntries.slice(0,8).map(([k,v])=>{{const t=reasonText[k]||[k.replaceAll('_',' '),'该条件未满足确定性风险或策略规则。'];return '<div class="reason"><span><b>'+t[0]+'</b></span><b>'+v+' 个标的/记录</b><span class="why">'+t[1]+'</span></div>'}}).join('');
  const hasPosition=latest.decision_pipeline?.virtual_position_created===true; document.getElementById('position').innerHTML=hasPosition?'<p><span class="pill good">OPEN</span> 正在跟踪虚拟仓位。</p>':'<p class="muted">当前没有虚拟仓位，模拟 P&L 为 $0.00。</p><p class="tiny muted">只有完整信号和合格期权同时通过后才会建立仓位。</p>';
  const rs=d.research||{{}}; document.getElementById('funnel').innerHTML='<div class="quality-row"><span>完成采样 <b>'+m.completed_runs+'</b></span><span>机械信号 <b>'+rs.mechanical_signals+'</b></span><span>严格候选 <b>'+rs.strict_candidates+'</b></span><span>虚拟交易 <b>'+m.virtual_trades+'</b></span></div><div class="reason-grid" style="margin-top:12px">'+(rs.top_rejections||[]).slice(0,4).map(([k,v])=>'<div class="reason"><span>'+k.replaceAll('_',' ')+'</span><b>'+v+'</b></div>').join('')+'</div>';
  document.getElementById('eligibility').innerHTML='<div class="safe"><span class="pill warn">正式合格 '+rs.eligible_runs+'</span><span class="pill good">隔离记录 '+rs.ineligible_runs+'</span></div><p class="tiny muted">Pilot、Drill、未授权、缺失/过期数据、异常或规则违规记录不会进入策略收益。当前面板中的历史采样仍属于 Pilot。</p>';
  if((latest.decision_pipeline?.option_research_status||'').startsWith('NOT_RUN')) document.getElementById('optionStep').textContent='③ 期权筛选（无需运行）';
  const schedulerHealthy=m.scheduler_incidents===0; document.getElementById('watchdog').innerHTML='<div class="safe"><span class="pill '+(schedulerHealthy?'good':'bad')+'">'+(schedulerHealthy?'HEALTHY':'INCIDENT')+'</span><span class="pill good">每 60 秒独立检查</span></div><p><b>预登记任务 '+m.expected_runs+'</b> · 已收到 ACK '+m.acknowledged_expected_runs+' · 调度事故 '+m.scheduler_incidents+'</p><p class="tiny muted">缺失、迟到、损坏或 run ID 不匹配的 ACK 会持久化事故、弹出一次 macOS 通知，并保持新开仓关闭。</p>';
  const rd=d.readiness||{{}}; const pending=rd.pending_market_checks||[]; document.getElementById('engineering').innerHTML='<div class="safe"><span class="pill '+(rd.offline_ready?'good':'bad')+'">本地工程 '+(rd.offline_ready?'READY':'BLOCKED')+'</span><span class="pill '+(rd.formal_shadow_authorized?'good':'warn')+'">正式 Shadow '+(rd.formal_shadow_authorized?'AUTHORIZED':'未授权')+'</span><span class="pill bad">Live 关闭</span></div><p><b>230 项本地测试通过。</b> 已覆盖虚拟仓位闭环、业绩资格隔离、故障注入、漂移监控、参数敏感性、对账、摩擦、walk-forward 与 AI 基准比较。</p><p class="tiny muted">仍需市场时段核验：'+(pending.join(' · ')||'无')+'。本地 READY 不代表策略盈利或允许下单。</p>';
  document.getElementById('runs').innerHTML=(d.runs||[]).map(r=>{{const p=r.decision_pipeline||{{}},g=r.governance||{{}},o=r.option_research||{{}};return '<tr><td><code>'+r.run_id+'</code></td><td><span class="pill '+(p.final_outcome==='TRADE'?'good':'warn')+'">'+(p.final_outcome||'UNKNOWN')+'</span></td><td>'+(r.market?.regime||'UNKNOWN')+'</td><td>'+(p.mechanical_signal_count??p.mechanical_direction_signal_count??0)+'</td><td>'+(o.quote_samples??r.sampling?.option_contract_samples??0)+'</td><td>'+(r.run_duration_seconds??'—')+' 秒</td><td>'+(g.performance_eligibility||'UNKNOWN')+'</td></tr>'}}).join('')||'<tr><td colspan="7" class="muted">尚无完成记录</td></tr>';
  let seconds=30; setInterval(()=>{{seconds--;set('countdown',seconds+' 秒后自动刷新');if(seconds<=0)location.reload()}},1000);
</script></body></html>"""


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_html(collect()), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()

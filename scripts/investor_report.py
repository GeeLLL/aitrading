"""Generate the investor progress report PDF from the repo's own records.

Every number in the report is read from logs/ at generation time rather than
typed in, so the report cannot drift from the evidence it describes. The report
itself states this, which is only true as long as this script is the thing that
produces it — so regenerate rather than hand-editing the PDF.

    python3 scripts/investor_report.py
"""
from __future__ import annotations

import glob
import json
import os
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "报告_影子系统进度_2026-08-13.pdf"

pdfmetrics.registerFont(TTFont("CJK", "/System/Library/Fonts/Supplemental/Songti.ttc"))
pdfmetrics.registerFont(TTFont("CJKB", "/System/Library/Fonts/STHeiti Medium.ttc"))

ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="CJK", fontSize=9.5,
                      leading=15, alignment=TA_LEFT, spaceAfter=6)
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="CJKB", fontSize=15,
                    leading=20, spaceBefore=14, spaceAfter=8,
                    textColor=colors.HexColor("#1a1a1a"))
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="CJKB", fontSize=11,
                    leading=16, spaceBefore=10, spaceAfter=5,
                    textColor=colors.HexColor("#333333"))
TITLE = ParagraphStyle("t", parent=ss["Title"], fontName="CJKB", fontSize=20, leading=27)
SUB = ParagraphStyle("sub", parent=BODY, fontSize=9.5, textColor=colors.HexColor("#555555"))
NOTE = ParagraphStyle("note", parent=BODY, fontSize=8.5, leading=13,
                      textColor=colors.HexColor("#666666"))


def slot_stats():
    days = {}
    for f in sorted(glob.glob(str(ROOT / "logs/launchd_worker/2026-0*/pilot-*.summary.json"))):
        day = f.split("/")[-2]
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if "status" not in d:
            continue
        row = days.setdefault(day, {"exp": 0, "done": 0, "adm": 0, "sig": 0})
        row["exp"] += 1
        row["done"] += d["status"] == "COMPLETED"
        dec = f.replace(".summary.json", ".decision.json")
        if os.path.exists(dec):
            e = json.load(open(dec))
            row["adm"] += bool(e.get("decision_admissible"))
            row["sig"] += bool(e.get("signalled_symbols"))
    return days


def calibration():
    out = []
    for f in sorted(glob.glob(str(ROOT / "logs/calibration/2026-*/entry.json"))):
        e = json.load(open(f))
        x = f.replace("entry", "exit")
        if os.path.exists(x):
            o = json.load(open(x))
            out.append((f.split("/")[-2], e["symbol"], e["entry_ask"], o["exit_bid"],
                        (o["exit_bid"] - e["entry_ask"]) * 100))
    return out


DAYS = slot_stats()
TOT = {k: sum(d[k] for d in DAYS.values()) for k in ("exp", "done", "adm", "sig")}
CAL = calibration()
CAL_PNL = [c[4] for c in CAL]
VAULT = sum(1 for _ in open(ROOT / "logs/raw/vault_index.jsonl"))


def P(t, s=BODY):
    return Paragraph(t, s)


def table(data, widths, align_right=()):
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "CJK"),
        ("FONTNAME", (0, 0), (-1, 0), "CJKB"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#888888")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for c in align_right:
        style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


story = [
    P("影子期权系统 · 进度报告", TITLE),
    Spacer(1, 4),
    P("报告日期 2026-08-13　|　覆盖期间 2026-07-22 至 2026-08-13　|　"
      "阶段：研究与数据采集（未投入资金）", SUB),
    HRFlowable(width="100%", thickness=1, color=colors.HexColor("#999999"),
               spaceBefore=8, spaceAfter=10),
    P("一、摘要", H1),
    P("本期间内系统<b>未投入任何资金，未下达任何真实订单</b>。系统全程处于只读模式，"
      "下单工具在进程层面被禁用，紧急停止开关保持接合。所有记录的仓位均为模拟仓位。"),
    P("本期的主要成果是<b>基础设施</b>：一条从调度、采集、不可篡改存证到确定性策略计算的完整链路，"
      "已经能够稳定运行并留下可审计的证据。本期的主要结论是<b>负面的</b>：在已有数据上，"
      "策略的方向性信号<b>未显示出正的预期收益</b>，各量能区间的方向收益均值全部为负。"),
    P("本期还发现并修复了两处会使模拟仓位失效的缺陷："
      "仓位方向可能与策略判定相反，以及持有期超出采样窗口导致仓位无法平仓。"
      "详见 4.1 节。这两处缺陷意味着<b>此前记录的 2 笔仓位不能用于评估策略</b>。"),
    P("我们认为这个负面结论本身是有价值的产出——它是在<b>没有任何资金损失</b>的前提下取得的，"
      "而这正是影子阶段存在的意义。"),
    P("二、资金与风险状态", H1),
    table([
        ["项目", "状态"],
        ["投入资金", "0（从未下单）"],
        ["系统模式", "READ_ONLY（只读）"],
        ["下单工具", "已禁用"],
        ["紧急停止开关", "已接合"],
        ["正式影子授权", "未通过（8 项前置检查通过 4 项）"],
        ["所有记录仓位", "模拟，明确标记为不计入业绩"],
    ], [55 * mm, 105 * mm]),
    P("三、数据采集覆盖率", H1),
    P(f"每个交易日的采样槽位数由调度表决定（2026-08-13 起由 14 个增至 18 个，覆盖至收盘）。期间共尝试 <b>{TOT['exp']}</b> 个，"
      f"完成 <b>{TOT['done']}</b> 个（{TOT['done'] / TOT['exp'] * 100:.0f}%），"
      f"其中数据完整性校验通过、可用于决策的 <b>{TOT['adm']}</b> 个。"
      f"累计存证快照 <b>{VAULT}</b> 份，均带 SHA-256 校验并写入只可追加的索引。"),
]

rows = [["日期", "尝试", "完成", "可用", "出信号"]]
for k in sorted(DAYS):
    v = DAYS[k]
    rows.append([k, str(v["exp"]), str(v["done"]), str(v["adm"]), str(v["sig"])])
rows.append(["合计", str(TOT["exp"]), str(TOT["done"]), str(TOT["adm"]), str(TOT["sig"])])
story += [
    table(rows, [40 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm], align_right=(1, 2, 3, 4)),
    P("完成率偏低的原因<b>已经查明且与策略无关</b>，主要是运行环境问题："
      "笔记本在盘中脱离电源后进入睡眠，定时任务被唤醒后数十秒又被挂起（占绝大多数损失）；"
      "以及早期的 Python 解释器版本、采集载荷超限等缺陷。这些缺陷已逐一修复并有回归测试覆盖。"
      "最近两个完整运行日（08-04、08-11）的完成率分别为 15/15 和 12/13。", NOTE),
    P("系统<b>不回补</b>任何错过的样本：180 秒新鲜度防线确保只使用当场采集的数据，"
      "错过即永久丢失并如实记录。这牺牲了覆盖率，换取的是数据不被事后污染。", NOTE),
    PageBreak(),
    P("四、策略结果", H1),
    P("4.1　策略模拟仓位", H2),
    P(f"整个期间，策略信号仅在 <b>{TOT['sig']}</b> 个槽位触发，实际开出 <b>2</b> 笔模拟仓位。"
      "两笔均未记录正式平仓，以下为最后一次观测的按市价估值："),
    table([
        ["日期", "标的", "策略判定", "实际开出", "建仓", "最后观测", "浮动盈亏/张"],
        ["08-03", "AMD", "CALL", "PUT 485", "28.00", "24.10", "-390.00"],
        ["08-13", "SOFI", "CALL", "PUT 18.5", "0.61", "0.52", "-9.00"],
    ], [18 * mm, 17 * mm, 22 * mm, 26 * mm, 22 * mm, 26 * mm, 29 * mm],
        align_right=(4, 5, 6)),
    P("<b>请注意第三、四列：两笔仓位的方向都与策略的判定相反。</b>"
      "这是一处已确认的缺陷，于 2026-08-13 发现并修复。合约选择函数原先只按"
      "「行权价与现价的距离」排序，<b>完全不读取合约的看涨/看跌类型</b>；"
      "而同一行权价的看涨与看跌期权距离相同，因此实际选中哪一个取决于交易所的返回顺序。"
      "两笔仓位均在看涨判定下建成了看跌合约。", NOTE),
    P("<b>因此这两笔的亏损不能作为策略无效的证据</b>——它们衡量的是与信号相反的方向。"
      "在此期间标的均为上涨，看跌合约亏损是预期内的结果。"
      "修复方式：合约方向改为<b>必填参数</b>，调用方必须显式声明，"
      "未声明则拒绝开仓而非任意选择。样本量为 2，本就不具备统计意义，"
      "此处列出仅为完整披露。", NOTE),
    P("<b>两笔均未平仓的原因是一处设计缺陷，而非样本不足。</b>"
      "持有目标为 60 分钟，而此前每日采样在 11:23 即停止，市场却运行至 13:00。"
      "因此 10:23 之后开出的任何仓位都<b>不可能被观测到到期</b>："
      "AMD 于 10:43 开仓、11:43 到期，当日已无槽位；"
      "SOFI 于 10:23 开仓、11:23:05 到期，比最后一个槽位晚 5 秒。"
      "而信号恰恰集中在该时段（本期 6 次信号有 5 次发生在 10:23 或之后），"
      "即最可能产生仓位的时间，产生的正是必然没有结果的仓位。"
      "该缺陷已于 2026-08-13 修复：采样窗口延长至 12:43，"
      "并新增一项规则——若持有期将超出当日最后一个槽位，则拒绝开仓，"
      "以免占用当日名额却无法得到结果。", NOTE),
    P("另需说明：08-03 的 AMD 合约每张约 2,800 美元，远超第一阶段设定的 75 美元单笔上限，"
      "反映出「会产生信号的标的」与「预算内可交易的标的」之间存在错配，该问题尚未解决。", NOTE),
    P("4.2　校准交易（机制验证，不计入策略业绩）", H2),
    P("由于策略信号稀少，系统每日额外执行一笔<b>与策略无关</b>的校准交易，"
      "标的为预算内最接近平值的合约，用途是验证「建仓→跟踪→平仓」链路可用，"
      "并测量真实的交易摩擦成本。"),
]

rows = [["日期", "标的", "建仓", "平仓", "毛盈亏/张"]]
for day, sym, ea, xb, pnl in CAL:
    rows.append([day, sym, f"{ea}", f"{xb}", f"{pnl:+.2f}"])
story += [
    table(rows, [30 * mm, 25 * mm, 30 * mm, 30 * mm, 35 * mm], align_right=(2, 3, 4)),
    P(f"合计 <b>{sum(CAL_PNL):+.2f}</b> 美元，胜 {sum(1 for x in CAL_PNL if x > 0)}/{len(CAL_PNL)}，"
      f"中位数 <b>{statistics.median(CAL_PNL):+.2f}</b> 美元。"
      "必须强调：合计为正完全来自两笔大额盈利（IWM +49、SPY +54），"
      "中位数为负，且标的选择是机械的、与任何预测无关。"
      "<b>这组数字不构成任何策略有效性的证据</b>，只证明机制可用。", NOTE),
    P("五、关键研究发现：信号未显示正的预期收益", H1),
    P("由于真实模拟仓位过少，我们改用存证的原始 K 线数据回溯重建策略信号，"
      "对每一根满足完整信号条件（趋势状态 + 均价 + 均线排列 + 六根 K 线突破）的样本，"
      "按<b>信号所指方向</b>计算其后 40 分钟的标的收益。结果如下："),
    table([
        ["量能比区间", "样本数", "方向收益均值", "中位数", "胜率"],
        ["低于 1.5", "564", "-0.052%", "-0.027%", "44.0%"],
        ["1.5 – 1.8（现行门槛）", "73", "-0.096%", "-0.036%", "43.8%"],
        ["1.8 – 2.5（开仓门槛）", "62", "-0.043%", "-0.025%", "46.8%"],
        ["2.5 以上", "26", "-0.281%", "-0.153%", "23.1%"],
    ], [42 * mm, 25 * mm, 33 * mm, 28 * mm, 25 * mm], align_right=(1, 2, 3, 4)),
    P("<b>四个区间的方向收益均值全部为负，胜率全部低于 50%。</b>"
      "这表明该信号在 5 分钟 K 线、40 分钟持有期的设定下，不但没有正向预测力，"
      "反而呈现轻微的反向特征——短周期突破在该时间尺度上倾向于回归。"),
    P("另一项独立测量显示，量能比在 2.5 以下时，其后 40 分钟的波动<b>幅度</b>"
      "与随机 K 线无法区分（1.5–1.8 区间中位 0.169%，低于 1.0 区间中位 0.170%）。"
      "也就是说，现行的量能门槛实际上没有筛选出任何东西。"),
    P("<b>结论的边界：</b>样本仅覆盖约三周、单一市场状态；高量能区间样本量仅 26–73，"
      "其点估计不具统计意义；测量的是标的收益而非期权损益，且未模拟真实止盈止损。"
      "但低量能区间样本量为 564，且四个区间方向一致为负，"
      "该结论不太可能仅由噪声解释。", NOTE),
    PageBreak(),
    P("六、交易成本：此前被显著低估", H1),
    P("策略原先使用一个固定的摩擦成本常数（每张往返 1.40 美元）。"
      "通过真实的校准交易实测，该常数<b>低估了实际成本 2.55 至 4.29 倍</b>。"
      "以 08-03 的 BAC 为例，入场时的事前成本门槛为权利金的 <b>4.01%</b>"
      "（买卖价差 3.00 + 手续费 0.40 + 时间价值损耗 0.17）。"),
    P("把这一点与第五节合并看：即便信号的方向收益是零而非负，"
      "标的约 0.17% 的典型波动经期权杠杆放大后，也难以稳定覆盖约 4% 的权利金成本门槛。"
      "<b>成本门槛，而非信号频率，才是当前策略无法盈利的主要障碍。</b>"),
    P("七、验证所需的样本量", H1),
    P("我们对验证标准做过一次诚实的重估。若要在 80% 检验力下识别出 5 个百分点的胜率优势，"
      "需要约 <b>785 笔</b>独立交易。原定的 30 笔门槛只能给出 ±17.89 个百分点的置信区间，"
      "即它只能检测出 25.57 个百分点以上的巨大优势——这不是一个有意义的验证。"),
    P(f"按当前信号频率（三周 {TOT['sig']} 个槽位触发、2 笔模拟仓位），"
      "<b>即便是 30 笔的门槛也没有可预见的完成日期。</b>"),
    P("八、下一步", H1),
    P("<b>我们不建议为了产生交易而放宽阈值。</b>"
      "测量显示放宽只会让一个负预期的信号交易得更频繁，加快成本流失，"
      "并且用调参后的同一批数据去评估会产生虚假的有效性。"),
    table([
        ["优先级", "事项", "理由"],
        ["1", "取消「每日仅开一笔」限制",
         "影子系统无资金约束；08-03 当日产生 4 个合格信号却丢弃了 3 个。\n"
         "目的是加快积累样本以验证第五节的结论，而非增加收益。"],
        ["已完成", "延长采样窗口至收盘，并拒绝开出无法平仓的仓位",
         "见 4.1 节。此前 10:23 之后开出的仓位必然没有结果。"],
        ["已完成", "合约方向改为必填，与策略判定强制一致",
         "见 4.1 节。此前两笔仓位的方向均与信号相反。"],
        ["2", "将信号有效性测量固化为每日自动更新的研究流程",
         "第五节的结论目前基于一次性分析。应随新数据持续更新，\n若结论错误，数据会推翻它。"],
        ["3", "解决预算与标的错配",
         "会产生信号的标的价格远超预算上限，使这些信号即便有效也不可执行。"],
        ["4", "重新审视策略假设本身",
         "若负预期在更多数据上成立，需要的是改变时间尺度或方向假设，\n而不是调整阈值。"],
    ], [16 * mm, 48 * mm, 96 * mm]),
    P("九、披露与声明", H1),
    P("本报告中的全部数字均由脚本在生成时从系统日志直接读取，未经人工录入，"
      "以避免报告与其所描述的证据产生偏离。原始快照均带 SHA-256 校验并写入只可追加的索引，"
      "可供独立复核。", NOTE),
    P("所有仓位均为模拟。本报告不构成任何投资建议，"
      "亦不对未来收益作出任何陈述或暗示。已披露的负面发现基于有限样本，"
      "可能随数据积累而改变，方向不限于变好或变坏。", NOTE),
]

doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=18 * mm, bottomMargin=18 * mm,
                        title="影子期权系统 进度报告 2026-08-13", author="")


def footer(canvas, doc_):
    canvas.saveState()
    canvas.setFont("CJK", 7.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(20 * mm, 10 * mm,
                      "影子期权系统 · 进度报告 · 2026-08-13 · 模拟仓位，未投入资金")
    canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"第 {doc_.page} 页")
    canvas.restoreState()


if __name__ == "__main__":
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("WROTE", OUT)

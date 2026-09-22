#!/usr/bin/env python3
"""
半年报投资决策日报 — 专业 PDF 报告生成器
合并三块内容（信号漏斗 report.md + 全量表 + 公告日表）为单一 PDF。

机构级排版（reportlab + WQY 中文字体），仿量化研究报告风格。
读取: semiannual_investment.csv + semiannual_investment_report.md
输出: semiannual_investment_report.pdf
"""
import csv, sys, re, argparse
from datetime import datetime, date
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable,
    Table, TableStyle, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Paths ──
STORE = Path.home() / "fmdata/store/fundamentals"
CSV_FILE = STORE / "semiannual_investment.csv"
REPORT_MD = STORE / "semiannual_investment_report.md"
OUT_PDF = STORE / "semiannual_investment_report.pdf"

# ── Register fonts ──
pdfmetrics.registerFont(TTFont('WQY', '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'))
pdfmetrics.registerFont(TTFont('WQYBold', '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', subfontIndex=1))

# ── Color palette (Morgan Stanley / Goldman hybrid) ──
NAVY_DEEP   = colors.HexColor('#002B51')
NAVY        = colors.HexColor('#004B87')
BLUE_HERO   = colors.HexColor('#7399C6')
BLUE_LIGHT  = colors.HexColor('#ACD4F1')
BLACK_TEXT  = colors.HexColor('#231F20')
GRAY_DARK   = colors.HexColor('#58575A')
GRAY_MID    = colors.HexColor('#7C8A97')
GRAY_LINE   = colors.HexColor('#B0B5BC')
GRAY_BG     = colors.HexColor('#F2F4F6')
WHITE       = colors.white
GREEN       = colors.HexColor('#00875D')
GREEN_BG    = colors.HexColor('#E6F4EF')
YELLOW_BG   = colors.HexColor('#FFF4E0')
RED         = colors.HexColor('#E52135')
RED_BG      = colors.HexColor('#FCE8EB')

# ── Styles ──
s = lambda name, **kw: ParagraphStyle(name, **kw)
cover_title = s('CoverTitle', fontName='WQYBold', fontSize=26, leading=34, textColor=WHITE, alignment=TA_CENTER, spaceAfter=10)
cover_sub   = s('CoverSub', fontName='WQY', fontSize=14, leading=20, textColor=BLUE_LIGHT, alignment=TA_CENTER, spaceAfter=6)
cover_info  = s('CoverInfo', fontName='WQY', fontSize=10, leading=15, textColor=GRAY_MID, alignment=TA_CENTER, spaceAfter=3)
section     = s('Section', fontName='WQYBold', fontSize=14, leading=20, textColor=NAVY_DEEP, spaceBefore=14, spaceAfter=6)
sub_section = s('Sub', fontName='WQYBold', fontSize=11, leading=16, textColor=NAVY, spaceBefore=8, spaceAfter=4)
body        = s('Body', fontName='WQY', fontSize=9, leading=14, textColor=BLACK_TEXT, spaceAfter=4, alignment=TA_JUSTIFY)
body_c      = s('BodyC', fontName='WQY', fontSize=9, leading=14, textColor=BLACK_TEXT, alignment=TA_CENTER)
small       = s('Small', fontName='WQY', fontSize=8, leading=11, textColor=GRAY_DARK)
small_c     = s('SmallC', fontName='WQY', fontSize=8, leading=11, textColor=BLACK_TEXT, alignment=TA_CENTER)
disclaimer  = s('Disc', fontName='WQY', fontSize=7.5, leading=11, textColor=GRAY_MID, alignment=TA_JUSTIFY)
tbl_hdr     = s('TblHdr', fontName='WQYBold', fontSize=7.5, leading=10, textColor=WHITE, alignment=TA_CENTER)
tbl_cell    = s('TblCell', fontName='WQY', fontSize=7.5, leading=10, textColor=BLACK_TEXT, alignment=TA_CENTER)
tbl_cell_l  = s('TblCellL', fontName='WQY', fontSize=7.5, leading=10, textColor=BLACK_TEXT, alignment=TA_LEFT)


def fmt(v, spec=''):
    """Safe format numeric."""
    try:
        if v is None or v == '' or (isinstance(v, float) and v != v):
            return '--'
        return format(float(v), spec)
    except Exception:
        return str(v) if v else '--'


def fmtpct(v, digits=0, sign=True):
    """Format a percentage value: v is in percent units (e.g. 30.69 means +30.69%).
    Produces clean output like '+31%' or '-67%'. No float-noise."""
    try:
        if v is None or v == '' or (isinstance(v, float) and v != v):
            return '--'
        f = float(v)
        prefix = '+' if sign and f >= 0 else ''
        return f"{prefix}{f:.{digits}f}%"
    except Exception:
        return '--'


def tier_color(score):
    if score >= 55: return GREEN_BG
    if score >= 45: return YELLOW_BG
    return RED_BG


def load_rows(csv_path):
    if not csv_path.exists():
        print(f"ERROR: {csv_path} 不存在", file=sys.stderr); sys.exit(1)
    with open(csv_path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def page_decorations(canvas, doc):
    """页眉页脚 — 除封面外所有页."""
    canvas.saveState()
    pg = canvas.getPageNumber()
    if pg == 1:
        canvas.restoreState(); return
    # Header
    canvas.setFont('WQY', 7.5)
    canvas.setFillColor(GRAY_MID)
    canvas.drawString(1.5*cm, A4[1]-1.0*cm, "半年报投资决策日报")
    canvas.drawRightString(A4[0]-1.5*cm, A4[1]-1.0*cm, datetime.now().strftime('%Y-%m-%d'))
    canvas.setStrokeColor(GRAY_LINE); canvas.setLineWidth(0.4)
    canvas.line(1.5*cm, A4[1]-1.15*cm, A4[0]-1.5*cm, A4[1]-1.15*cm)
    # Footer
    canvas.drawString(1.5*cm, 1.0*cm, "⚠️ 量化系统自动生成 · 不构成投资建议")
    canvas.drawRightString(A4[0]-1.5*cm, 1.0*cm, f"第 {pg} 页")
    canvas.restoreState()


def build_cover(rows, today_str, last_td):
    """封面页 — 深海军蓝背景."""
    story = []
    n = len(rows)
    green = sum(1 for r in rows if float(r.get('total_score',0) or 0) >= 55)
    yellow = sum(1 for r in rows if 45 <= float(r.get('total_score',0) or 0) < 55)
    red = sum(1 for r in rows if float(r.get('total_score',0) or 0) < 45)

    # Navy background block
    bg = Table([['']], colWidths=[A4[0]-3*cm], rowHeights=[A4[1]-2*cm])
    bg.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY_DEEP)]))
    story.append(bg)

    # Overlay text via a spacer + paragraphs (reportlab overlay is complex; use a second table on top)
    # Simpler: use a 1-cell table with navy bg and stack paragraphs inside
    cover_content = [
        [Paragraph("半年报投资决策日报", cover_title)],
        [Paragraph(f"Semi-Annual Investment Decision Report", cover_sub)],
        [Spacer(1, 0.8*cm)],
        [Paragraph(f"{today_str}", s('d1', fontName='WQYBold', fontSize=16, leading=22, textColor=WHITE, alignment=TA_CENTER))],
        [Spacer(1, 0.4*cm)],
        [Paragraph(f"最后交易日: {last_td}", cover_info)],
        [Paragraph(f"覆盖标的: {n} 只", cover_info)],
        [Spacer(1, 0.6*cm)],
        [Paragraph(f"🟢 强信号 {green}  ·  🟡 观察 {yellow}  ·  🔴 回避 {red}",
                   s('tier', fontName='WQYBold', fontSize=12, leading=18, textColor=BLUE_LIGHT, alignment=TA_CENTER))],
        [Spacer(1, 1.2*cm)],
        [Paragraph("6因子 + 连续交互 + 贝叶斯收缩 + 资金流向Proxy", cover_info)],
        [Paragraph("v3.4 · 第五轮 DeerFlow 审计迭代", cover_info)],
    ]
    cover_tbl = Table(cover_content, colWidths=[A4[0]-3*cm])
    cover_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),NAVY_DEEP),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),2),
        ('BOTTOMPADDING',(0,0),(-1,-1),2),
    ]))
    # Replace story: just the cover table (full page navy)
    story = [cover_tbl, PageBreak()]
    return story


def build_exec_summary(rows):
    """执行摘要 — 三档分布 + TOP3 + 风险."""
    story = [Paragraph("一、执行摘要", section), HRFlowable(width='100%', thickness=0.8, color=BLUE_HERO, spaceAfter=6)]
    n = len(rows)
    green = [r for r in rows if float(r.get('total_score',0) or 0) >= 55 and not r.get('_veto_flags')]
    yellow = [r for r in rows if (45 <= float(r.get('total_score',0) or 0) < 55) or (r.get('_veto_flags') and float(r.get('total_score',0) or 0) >= 45)]
    red = [r for r in rows if float(r.get('total_score',0) or 0) < 45]
    veto = [r for r in rows if r.get('_veto_flags')]

    # Summary table
    sum_tbl = Table([
        [Paragraph(f"<b>{len(green)}</b>", s('g',fontName='WQYBold',fontSize=20,textColor=GREEN,alignment=TA_CENTER)),
         Paragraph(f"<b>{len(yellow)}</b>", s('y',fontName='WQYBold',fontSize=20,textColor=colors.HexColor('#CC8800'),alignment=TA_CENTER)),
         Paragraph(f"<b>{len(red)}</b>", s('r',fontName='WQYBold',fontSize=20,textColor=RED,alignment=TA_CENTER)),
         Paragraph(f"<b>{len(veto)}</b>", s('v',fontName='WQYBold',fontSize=20,textColor=GRAY_DARK,alignment=TA_CENTER))],
        [Paragraph("强信号", small_c), Paragraph("观察", small_c), Paragraph("回避", small_c), Paragraph("含否决标记", small_c)],
    ], colWidths=[4*cm]*4)
    sum_tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(0,-1),GREEN_BG),
        ('BACKGROUND',(1,0),(1,-1),YELLOW_BG),
        ('BACKGROUND',(2,0),(2,-1),RED_BG),
        ('BACKGROUND',(3,0),(3,-1),GRAY_BG),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
        ('BOX',(0,0),(-1,-1),0.5,GRAY_LINE),
        ('LINEAFTER',(0,0),(2,-1),0.5,WHITE),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 0.4*cm))

    # TOP3
    story.append(Paragraph("TOP 3 强信号标的", sub_section))
    for i, r in enumerate(sorted(green, key=lambda x: float(x.get('total_score',0)), reverse=True)[:3], 1):
        name = r.get('name','')
        code = r.get('code','')
        score = float(r.get('total_score',0) or 0)
        h1yy = fmtpct(r.get('h1_prof_forecast_yoy_pct'))
        q2qq = fmtpct(r.get('q2_prof_implied_qoq_pct'))
        ind = r.get('industry','')
        story.append(Paragraph(
            f"<b>{i}. {code} {name}</b> &nbsp;|&nbsp; 评分 <b>{score:.1f}</b> &nbsp;|&nbsp; H1 {h1yy} &nbsp; Q2QoQ {q2qq} &nbsp;|&nbsp; {ind}",
            body))

    # Risk flags
    if veto:
        story.append(Paragraph("⚠️ 风险提示（否决标记标的）", sub_section))
        veto_types = {}
        for r in veto:
            for f in r['_veto_flags'].split(','):
                veto_types[f] = veto_types.get(f, 0) + 1
        flags_str = " · ".join(f"{k}: {v}只" for k,v in sorted(veto_types.items(), key=lambda x:-x[1]))
        story.append(Paragraph(flags_str, body))

    return story


def build_signal_funnel(rows):
    """信号漏斗 — 强/观察/回避 三段（report.md 的核心内容）."""
    story = [Paragraph("二、信号漏斗", section), HRFlowable(width='100%', thickness=0.8, color=BLUE_HERO, spaceAfter=6)]
    has_veto = lambda r: bool(r.get('_veto_flags'))
    is_filtered = lambda r: r.get('is_filtered','False') in ('True','true','1')

    green = [r for r in rows if float(r.get('total_score',0) or 0) >= 55 and not is_filtered(r) and not has_veto(r)]
    yellow = [r for r in rows if ((45 <= float(r.get('total_score',0) or 0) < 55) or (has_veto(r) and float(r.get('total_score',0) or 0) >= 45)) and not is_filtered(r)]
    red = [r for r in rows if float(r.get('total_score',0) or 0) < 45 or is_filtered(r)]

    def signal_tbl(rws, bg):
        if not rws: return [Paragraph("（无）", small), Spacer(1,0.2*cm)]
        hdr = ['排名','代码','简称','评分','覆盖度','核心信号','警告','一句话逻辑']
        data = [[Paragraph(h, tbl_hdr) for h in hdr]]
        for i, r in enumerate(rws, 1):
            sigs = []
            for k,lab in [('sue_score','SUE'),('eq_score','EQ'),('price_score','价格'),('precar_score','PreCAR'),('industry_score','行业'),('timing_score','早披露')]:
                v = r.get(k)
                try:
                    if v and float(v) > 65: sigs.append(lab+'↑')
                except: pass
            veto = r.get('_veto_flags','')
            warn = '⚠️盈余陷阱' if float(r.get('_interaction_bonus',0) or 0) < -3 else ''
            if veto: warn = f"🚫{veto}"
            logic_parts = []
            h1yy = fmtpct(r.get('h1_prof_forecast_yoy_pct'))
            if h1yy != '--': logic_parts.append(f"H1 {h1yy}")
            q2qq = fmtpct(r.get('q2_prof_implied_qoq_pct'))
            if q2qq != '--': logic_parts.append(f"Q2QoQ {q2qq}")
            data.append([
                Paragraph(str(int(float(r.get('score_rank',i) or i))), tbl_cell),
                Paragraph(r.get('code',''), tbl_cell),
                Paragraph(r.get('name','')[:8], tbl_cell_l),
                Paragraph(fmt(r.get('total_score'), '.1f'), tbl_cell),
                Paragraph(f"{float(r.get('_coverage_ratio',1) or 1):.0%}", tbl_cell),
                Paragraph('+'.join(sigs[:3]) or '--', tbl_cell),
                Paragraph(warn or '', tbl_cell),
                Paragraph(', '.join(logic_parts) or '待分析', tbl_cell_l),
            ])
        t = Table(data, colWidths=[1.0*cm,1.4*cm,1.8*cm,1.2*cm,1.3*cm,2.6*cm,2.0*cm,2.0*cm], repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),NAVY),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE, bg]),
            ('GRID',(0,0),(-1,-1),0.3,GRAY_LINE),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),
        ]))
        return [t, Spacer(1, 0.25*cm)]

    story.append(Paragraph(f"🟢 强信号区（评分≥55，{len(green)}只）", sub_section))
    story.extend(signal_tbl(green, GREEN_BG))
    story.append(Paragraph(f"🟡 观察区（45-55 或含否决，{len(yellow)}只）", sub_section))
    story.extend(signal_tbl(yellow, YELLOW_BG))
    story.append(Paragraph(f"🔴 回避区（<45 或已过滤，{len(red)}只）", sub_section))
    story.extend(signal_tbl(red, RED_BG))
    return story


def build_full_table(rows, by='score'):
    """全量数据表（17列，横向放不下，用 A4 纵向 + 小字号）."""
    title = "四、全量数据表 · 按评分排序" if by=='score' else "三、全量数据表 · 按公告日排序"
    story = [Paragraph(title, section), HRFlowable(width='100%', thickness=0.8, color=BLUE_HERO, spaceAfter=6)]

    if by == 'date':
        rows = sorted(rows, key=lambda r: r.get('notice_date',''), reverse=True)
    else:
        rows = sorted(rows, key=lambda r: float(r.get('total_score',0) or 0), reverse=True)

    today_str = date.today().isoformat()
    hdrs = ['公告日','代码','简称','类型','预告H1','去年H1','H1YoY','Q1实','Q1YoY','Q2隐含','Q2YoY','Q2QoQ','SUE','EQ','价格','PreCAR','总分','等级']
    data = [[Paragraph(h, tbl_hdr) for h in hdrs]]
    for r in rows:
        ndate = r.get('notice_date','')[:10]
        if by=='date' and ndate == today_str: ndate = ndate + '🆕'
        score = float(r.get('total_score',0) or 0)
        tier = '🟢' if score >= 55 else ('🟡' if score >= 45 else '🔴')
        row = [
            Paragraph(ndate, tbl_cell),
            Paragraph(r.get('code',''), tbl_cell),
            Paragraph(r.get('name','')[:7], tbl_cell_l),
            Paragraph(r.get('forecast_type','')[:4], tbl_cell),
            Paragraph(fmt(r.get('forecast_mid_yi'),'.1f'), tbl_cell),
            Paragraph(fmt(r.get('h1_prior_profit_yi'),'.1f'), tbl_cell),
            Paragraph(fmtpct(r.get('h1_prof_forecast_yoy_pct')), tbl_cell),
            Paragraph(fmt(r.get('q1_profit_yi'),'.2f'), tbl_cell),
            Paragraph(fmtpct(r.get('q1_prof_yoy_pct')), tbl_cell),
            Paragraph(fmt(r.get('q2_profit_yi_implied'),'.2f'), tbl_cell),
            Paragraph(fmtpct(r.get('q2_prof_implied_yoy_pct')), tbl_cell),
            Paragraph(fmtpct(r.get('q2_prof_implied_qoq_pct')), tbl_cell),
            Paragraph(fmt(r.get('sue_score'),'.0f'), tbl_cell),
            Paragraph(fmt(r.get('eq_score'),'.0f'), tbl_cell),
            Paragraph(fmt(r.get('price_score'),'.0f'), tbl_cell),
            Paragraph(fmt(r.get('precar_score'),'.0f'), tbl_cell),
            Paragraph(fmt(r.get('total_score'),'.1f'), tbl_cell),
            Paragraph(tier, tbl_cell),
        ]
        data.append(row)

    # 18 cols on A4 portrait (17cm usable) → ~0.95cm each, tight but readable at 7.5pt
    cw = [1.25,1.0,1.4,0.9,0.95,0.95,0.85,0.85,0.85,0.95,0.85,0.85,0.65,0.55,0.65,0.75,0.75,0.55]
    cw = [c*cm for c in cw]
    t = Table(data, colWidths=cw, repeatRows=1)
    # Row backgrounds by tier
    styles = [('BACKGROUND',(0,0),(-1,0),NAVY),
              ('GRID',(0,0),(-1,-1),0.25,GRAY_LINE),
              ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
              ('TOPPADDING',(0,0),(-1,-1),2.5),('BOTTOMPADDING',(0,0),(-1,-1),2.5)]
    for i, r in enumerate(rows, 1):
        score = float(r.get('total_score',0) or 0)
        bg = GREEN_BG if score >= 55 else (YELLOW_BG if score >= 45 else RED_BG)
        styles.append(('BACKGROUND',(0,i),(-1,i),bg))
    t.setStyle(TableStyle(styles))
    story.append(t)
    return story


def build_data_quality(rows):
    """数据质量 + 模型说明."""
    story = [PageBreak(), Paragraph("五、数据质量与模型说明", section), HRFlowable(width='100%', thickness=0.8, color=BLUE_HERO, spaceAfter=6)]
    n = len(rows) or 1
    n_q1 = sum(1 for r in rows if r.get('q1_profit_yi'))
    n_ind = sum(1 for r in rows if r.get('industry'))
    n_mkt = sum(1 for r in rows if r.get('mkt_cap_yi'))
    n_price = sum(1 for r in rows if r.get('open_gap_pct'))

    dq = [
        ['指标','覆盖率','状态'],
        ['Q1 财报（cjpy）', f"{n_q1}/{n} ({n_q1/n:.0%})", '✅' if n_q1==n else '⚠️'],
        ['行业分类（tushare）', f"{n_ind}/{n} ({n_ind/n:.0%})", '✅' if n_ind==n else '⚠️'],
        ['市值（东财→代理）', f"{n_mkt}/{n} ({n_mkt/n:.0%})", '✅' if n_mkt==n else '⚠️ 代理失败已用静态值'],
        ['价格事件', f"{n_price}/{n} ({n_price/n:.0%})", '✅' if n_price>n/2 else '⚠️ PreCAR/Price 可能不准'],
    ]
    data = [[Paragraph(c, tbl_hdr if i==0 else (tbl_cell_l if j==0 else tbl_cell)) for j,c in enumerate(row)] for i,row in enumerate(dq)]
    t = Table(data, colWidths=[5*cm, 5*cm, 7*cm])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),NAVY),
                           ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,GRAY_BG]),
                           ('GRID',(0,0),(-1,-1),0.3,GRAY_LINE),
                           ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                           ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    story.append(t)
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("模型说明", sub_section))
    notes = [
        "<b>6因子体系</b>：SUE（盈余惊喜）+ EQ（Q1盈余质量）+ Price（公告日价格确认）+ PreCAR（预告前异常收益反向）+ Industry（行业动量）+ Timing（披露时机）。",
        "<b>动态权重</b>：预告期 SUE↓/EQ↑，财报期 SUE↑↑。预告期 SUE 用季节性代理（置信度0.55），正式报期激活真SUE（置信度1.0）。",
        "<b>贝叶斯收缩</b>：覆盖度低的标的评分向均值50收缩，避免数据不全的高分假象。",
        "<b>数据源</b>：tushare（行业+预告）+ cjpy长江金工（财报）+ 东财（市值，套QG代理池）。",
        "<b>阶段置信度</b>：当前为预告期，SUE为代理值，建议<b>仅观察不重仓</b>。",
    ]
    for n_text in notes:
        story.append(Paragraph("• " + n_text, body))

    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("免责声明", sub_section))
    story.append(Paragraph(
        "本报告由量化系统自动生成，基于公开业绩预告/快报数据与历史财务数据，不构成任何投资建议。"
        "预告期数据置信度较低，模型存在过拟合与数据缺失风险。投资决策请结合基本面研究、估值判断与风险管理，"
        "过往表现不代表未来收益。", disclaimer))
    return story


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(OUT_PDF))
    ap.add_argument('--csv', default=str(CSV_FILE))
    args = ap.parse_args()

    csv_path = Path(args.csv)
    rows = load_rows(csv_path)
    today_str = datetime.now().strftime('%Y-%m-%d')
    last_td = rows[0].get('notice_date','')[:10] if rows else today_str

    out = Path(args.out)
    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            title="半年报投资决策日报", author="fmdata quant system")

    story = []
    story += build_cover(rows, today_str, last_td)
    story += build_exec_summary(rows)
    story += build_signal_funnel(rows)
    story += build_full_table(rows, by='date')
    story.append(PageBreak())
    story += build_full_table(rows, by='score')
    story += build_data_quality(rows)

    doc.build(story, onFirstPage=page_decorations, onLaterPages=page_decorations)
    size_kb = out.stat().st_size / 1024
    print(f"✅ PDF 生成: {out} ({size_kb:.0f} KB, {len(rows)} 只标的)")
    print(f"   路径: {out}")


if __name__ == "__main__":
    main()

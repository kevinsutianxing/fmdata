#!/usr/bin/env python3
"""
半年报追踪脚本 - 快报 + 预告 + Q2拆解 + 同比/环比
===================================================
数据源:
  - cjpy (长江金工/天软): 业绩快报 + 合并利润表 (Q1/Q2/H1历史)
  - akshare: stock_yjyg_em (业绩预告, 东财)
输出:
  - CSV: ~/fmdata/store/fundamentals/semiannual_tracker.csv
  - 每日报告: ~/fmdata/store/fundamentals/semiannual_report.md

增量: 每次全量刷新 (预告/快报数据集不大, akshare一次调用, cjpy批量快)
"""

import cjpy
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os, sys, argparse
import requests
import time as _time
from collections import Counter, defaultdict

# ---- Config ----
OUT_CSV = os.path.expanduser("~/fmdata/store/fundamentals/semiannual_tracker.csv")
OUT_REPORT = os.path.expanduser("~/fmdata/store/fundamentals/semiannual_report.md")
CJPY_BATCH = 500          # cjpy 批量大小
PERIOD_H1 = "20260630"    # 当前半年报截止日
PERIOD_Q1 = "20260331"    # 当前一季报截止日
PERIOD_H1_PRIOR = "20250630"  # 去年同期半年报
PERIOD_Q1_PRIOR = "20250331"  # 去年同期一季报

today = datetime.now()
TODAY_STR = today.strftime("%Y%m%d")
THIS_WEEK_START = (today - timedelta(days=today.weekday())).strftime("%Y%m%d")


def last_trading_day(ref_date=None):
    """Return the most recent trading day (Mon-Fri, non-holiday) before ref_date.
    Uses fmdata trade_calendar if available, falls back to weekday skip.
    """
    if ref_date is None:
        ref_date = today
    # Simple fallback: skip weekends
    d = ref_date - timedelta(days=1)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d -= timedelta(days=1)
    # Try fmdata trade_calendar for holiday check
    try:
        import requests as _req
        r = _req.get(f"http://127.0.0.1:1934/reference/calendar?date={d.strftime('%Y%m%d')}", timeout=3)
        if r.status_code == 200:
            is_trade = r.json().get("is_trade_day", True)
            while not is_trade:
                d -= timedelta(days=1)
                while d.weekday() >= 5:
                    d -= timedelta(days=1)
                r = _req.get(f"http://127.0.0.1:1934/reference/calendar?date={d.strftime('%Y%m%d')}", timeout=3)
                is_trade = r.json().get("is_trade_day", True) if r.status_code == 200 else True
    except Exception:
        pass  # fallback OK
    return d


LAST_TRADE_DAY = last_trading_day()


def eprint(*args, **kwargs):
    """Print to stderr (visible in logs, won't interfere with CSV output)."""
    print(*args, file=sys.stderr, **kwargs)


def to_cjpy_code(code):
    """Convert 6-digit A-share code to cjpy format (SZ/SH/BJ prefix)."""
    code = str(code).zfill(6)
    if code.startswith(('600', '601', '603', '605')):
        return f'SH{code}'
    elif code.startswith(('688', '689')):
        return f'SH{code}'
    elif code.startswith(('000', '001', '002', '003', '004', '300', '301')):
        return f'SZ{code}'
    elif code.startswith(('430', '830', '831', '832', '833', '834', '835', '836', '837', '838', '839', '920')):
        return f'BJ{code}'
    return None


def to_raw_code(cjpy_code):
    """Strip cjpy prefix to get 6-digit code."""
    if cjpy_code and len(cjpy_code) >= 8:
        return cjpy_code[2:]
    return str(cjpy_code).zfill(6)


# ============================================================
# Step 1: 业绩预告 (akshare → stock_yjyg_em)
# ============================================================
def fetch_forecasts():
    """拉取 H1 2026 业绩预告, 只取归母净利润指标."""
    eprint("[1/5] 拉取业绩预告...")
    try:
        df = ak.stock_yjyg_em(date=PERIOD_H1)
        if df is None or len(df) == 0:
            eprint("  预告: 暂无数据")
            return pd.DataFrame()
    except Exception as e:
        eprint(f"  预告拉取失败: {e}")
        return pd.DataFrame()

    # 只看归母净利润 (最重要的指标)
    df = df[df['预测指标'] == '归属于上市公司股东的净利润'].copy()
    if len(df) == 0:
        eprint("  预告: 无归母净利润指标")
        return pd.DataFrame()

    # 标准化
    df['code'] = df['股票代码'].astype(str).str.zfill(6)
    df['name'] = df['股票简称']
    df['notice_date'] = pd.to_datetime(df['公告日期'], errors='coerce')
    df['forecast_h1_profit'] = pd.to_numeric(df['预测数值'], errors='coerce')
    df['forecast_change_pct'] = pd.to_numeric(df['业绩变动幅度'], errors='coerce')  # H1 YoY %
    df['prior_h1_profit'] = pd.to_numeric(df['上年同期值'], errors='coerce')
    df['forecast_type'] = df['预告类型']  # 预增/预减/扭亏/略增/略减/续盈/续亏/首亏

    # Map forecast type to English
    type_map = {
        '预增': 'pre_increase', '预减': 'pre_reduction',
        '略增': 'slight_increase', '略减': 'slight_reduction',
        '扭亏': 'turn_profit', '首亏': 'first_loss',
        '续盈': 'continue_profit', '续亏': 'continue_loss',
        '不确定': 'uncertain',
    }
    df['forecast_type_en'] = df['forecast_type'].map(type_map).fillna('unknown')

    out = df[['code', 'name', 'notice_date', 'forecast_h1_profit',
              'forecast_change_pct', 'prior_h1_profit',
              'forecast_type', 'forecast_type_en']].copy()
    out['source'] = '预告'

    eprint(f"  预告: {len(out)} 只股票 (公告日期 {out['notice_date'].min().strftime('%Y-%m-%d') if not out['notice_date'].isna().all() else 'N/A'} ~ {out['notice_date'].max().strftime('%Y-%m-%d') if not out['notice_date'].isna().all() else 'N/A'})")
    return out


# ============================================================
# Step 2: 业绩快报 (cjpy → 业绩快报 表)
# ============================================================
def fetch_express_reports():
    """批量拉取 cjpy 业绩快报, 筛选 H1 2026."""
    eprint("[2/5] 拉取业绩快报...")
    try:
        stocks = cjpy.get_stocks(TODAY_STR)
    except Exception as e:
        eprint(f"  get_stocks 失败: {e}")
        return pd.DataFrame()

    all_parts = []
    for i in range(0, len(stocks), CJPY_BATCH):
        batch = stocks[i:i + CJPY_BATCH]
        try:
            chunk = cjpy.get_table_data(batch, '业绩快报')
            # Filter to current H1 period
            chunk = chunk[chunk['截止日'] == int(PERIOD_H1)]
            if len(chunk) > 0:
                all_parts.append(chunk)
        except Exception as e:
            eprint(f"  快报 batch {i//CJPY_BATCH}: {e}")
            continue

    if not all_parts:
        eprint("  快报: 暂无 H1 2026 数据 (正常,半年报截止日刚过)")
        return pd.DataFrame()

    df = pd.concat(all_parts, ignore_index=True)
    df = df.rename(columns={
        'CODE': 'code', 'NAME': 'name',
        '公布日': 'notice_date_raw', '截止日': 'report_date',
        '营业总收入': 'express_h1_revenue',
        '归属于母公司所有者净利润': 'express_h1_profit',
    })
    df['notice_date'] = pd.to_datetime(df['notice_date_raw'].astype(str), format='%Y%m%d', errors='coerce')
    df['source'] = '快报'

    out = df[['code', 'name', 'notice_date', 'express_h1_revenue',
              'express_h1_profit', 'source']].copy()

    eprint(f"  快报: {len(out)} 只股票")
    return out


# ============================================================
# Step 3: 合并利润表 (cjpy) - 只拉有预告/快报的股票
# ============================================================
def fetch_income_statements(codes):
    """拉取指定股票的合并利润表 (Q1 2026, H1 2025, Q1 2025)."""
    if not codes:
        return pd.DataFrame()

    # Convert codes to cjpy format
    cjpy_codes = []
    code_map = {}  # cjpy_code → raw_code
    for c in codes:
        cj = to_cjpy_code(c)
        if cj:
            cjpy_codes.append(cj)
            code_map[cj] = str(c).zfill(6)
        else:
            eprint(f"  无法转换代码: {c}")

    if not cjpy_codes:
        return pd.DataFrame()

    eprint(f"[3/5] 拉取合并利润表 ({len(cjpy_codes)} 只)...")
    cjpy_codes = list(set(cjpy_codes))
    all_parts = []
    needed_periods = {int(PERIOD_Q1), int(PERIOD_H1_PRIOR), int(PERIOD_Q1_PRIOR)}

    for i in range(0, len(cjpy_codes), CJPY_BATCH):
        batch = cjpy_codes[i:i + CJPY_BATCH]
        try:
            chunk = cjpy.get_table_data(batch, '合并利润表')
            chunk = chunk[chunk['截止日'].isin(needed_periods)]
            if len(chunk) > 0:
                all_parts.append(chunk)
        except Exception as e:
            eprint(f"  利润表 batch {i//CJPY_BATCH}: {e}")
            continue

    if not all_parts:
        eprint("  利润表: 无数据")
        return pd.DataFrame()

    df = pd.concat(all_parts, ignore_index=True)
    # Convert cjpy codes back to 6-digit
    df['code'] = df['CODE'].apply(to_raw_code)
    df = df.rename(columns={
        'NAME': 'name',
        '截止日': 'period', '营业收入': 'revenue',
        '归属于母公司所有者净利润': 'net_profit',
    })
    eprint(f"  利润表: {len(df)} 行, {df['code'].nunique()} 只股票")
    return df[['code', 'period', 'revenue', 'net_profit']]


# ============================================================
# Step 4: 计算 Q2 拆解 + 同比/环比
# ============================================================
def calculate_q2(forecasts, express, income):
    """核心计算: 对每只股票算 Q2 = H1 - Q1, 同比, 环比."""
    eprint("[4/5] 计算 Q2 拆解 + 同比/环比...")

    # Build Q1 2026 / H1 2025 / Q1 2025 lookup from income statement
    q1_2026 = {}
    h1_2025 = {}
    q1_2025 = {}

    if len(income) > 0:
        for _, row in income.iterrows():
            code = row['code']
            period = row['period']
            rev = row.get('revenue')
            prof = row.get('net_profit')
            if period == int(PERIOD_Q1):
                q1_2026[code] = {'revenue': rev, 'profit': prof}
            elif period == int(PERIOD_H1_PRIOR):
                h1_2025[code] = {'revenue': rev, 'profit': prof}
            elif period == int(PERIOD_Q1_PRIOR):
                q1_2025[code] = {'revenue': rev, 'profit': prof}

    rows = []

    # ---- Process 快报 ----
    if len(express) > 0:
        for _, r in express.iterrows():
            code = r['code']
            name = r['name']
            notice_date = r['notice_date']
            h1_rev = r.get('express_h1_revenue')
            h1_prof = r.get('express_h1_profit')
            q1 = q1_2026.get(code, {})

            row = _calc_one(code, name, notice_date, '快报',
                           h1_revenue=h1_rev, h1_profit=h1_prof,
                           q1_data=q1, h1_prior=h1_2025.get(code, {}),
                           q1_prior=q1_2025.get(code, {}),
                           forecast_type=None, forecast_change_pct=None)
            if row:
                rows.append(row)

    # ---- Process 预告 ----
    if len(forecasts) > 0:
        for _, r in forecasts.iterrows():
            code = r['code']
            name = r['name']
            notice_date = r['notice_date']
            h1_prof_forecast = r.get('forecast_h1_profit')
            q1 = q1_2026.get(code, {})

            row = _calc_one(code, name, notice_date, '预告',
                           h1_revenue=None, h1_profit=h1_prof_forecast,
                           q1_data=q1, h1_prior=h1_2025.get(code, {}),
                           q1_prior=q1_2025.get(code, {}),
                           forecast_type=r.get('forecast_type'),
                           forecast_change_pct=r.get('forecast_change_pct'))
            if row:
                rows.append(row)

    result = pd.DataFrame(rows)
    if len(result) > 0:
        # Sort: newest notice first, then by profit impact
        result = result.sort_values(['notice_date', 'q2_profit_yi'], ascending=[False, False])
    eprint(f"  计算完成: {len(result)} 条记录")
    return result


def _growth_rate(current, prior):
    """Safe growth rate: (current/prior - 1) * 100, handles zeros and negatives."""
    if current is None or prior is None or pd.isna(current) or pd.isna(prior):
        return None
    if prior == 0:
        return None  # Can't divide by zero
    return round((current / prior - 1) * 100, 2)


def _calc_one(code, name, notice_date, source, h1_revenue, h1_profit,
              q1_data, h1_prior, q1_prior, forecast_type, forecast_change_pct):
    """Calculate Q2 metrics for one company."""
    q1_rev = q1_data.get('revenue')
    q1_prof = q1_data.get('profit')
    h1p_rev = h1_prior.get('revenue')
    h1p_prof = h1_prior.get('profit')
    q1p_rev = q1_prior.get('revenue')
    q1p_prof = q1_prior.get('profit')

    # Q2 decomposition
    q2_revenue = None
    q2_profit = None
    if h1_revenue is not None and q1_rev is not None and not pd.isna(h1_revenue) and not pd.isna(q1_rev):
        q2_revenue = h1_revenue - q1_rev
    if h1_profit is not None and q1_prof is not None and not pd.isna(h1_profit) and not pd.isna(q1_prof):
        q2_profit = h1_profit - q1_prof

    # Prior year Q2
    q2_prior_revenue = None
    q2_prior_profit = None
    if h1p_rev is not None and q1p_rev is not None and not pd.isna(h1p_rev) and not pd.isna(q1p_rev):
        q2_prior_revenue = h1p_rev - q1p_rev
    if h1p_prof is not None and q1p_prof is not None and not pd.isna(h1p_prof) and not pd.isna(q1p_prof):
        q2_prior_profit = h1p_prof - q1p_prof

    # Growth rates
    rev_yoy = _growth_rate(q2_revenue, q2_prior_revenue)
    rev_qoq = _growth_rate(q2_revenue, q1_rev)
    prof_yoy = _growth_rate(q2_profit, q2_prior_profit)
    prof_qoq = _growth_rate(q2_profit, q1_prof)

    # Also compute H1 growth (for reference)
    h1_prof_yoy = _growth_rate(h1_profit, h1p_prof)

    return {
        'code': code,
        'name': name,
        'source': source,
        'notice_date': notice_date.strftime('%Y-%m-%d') if hasattr(notice_date, 'strftime') else str(notice_date),
        'forecast_type': forecast_type,
        # H1 totals (from 快报 or 预告)
        'h1_revenue_yi': _fmt(h1_revenue, 1e8),       # 亿
        'h1_profit_yi': _fmt(h1_profit, 1e8),
        # Q2 decomposition
        'q1_revenue_yi': _fmt(q1_rev, 1e8),
        'q2_revenue_yi': _fmt(q2_revenue, 1e8),
        'q1_profit_yi': _fmt(q1_prof, 1e8),
        'q2_profit_yi': _fmt(q2_profit, 1e8),
        # YoY (Q2 vs Q2 prior year)
        'q2_rev_yoy_pct': rev_yoy,
        'q2_prof_yoy_pct': prof_yoy,
        # QoQ (Q2 vs Q1)
        'q2_rev_qoq_pct': rev_qoq,
        'q2_prof_qoq_pct': prof_qoq,
        # H1 growth reference
        'h1_prof_yoy_pct': h1_prof_yoy,
        # Raw values for debugging
        '_h1_revenue': h1_revenue,
        '_h1_profit': h1_profit,
        '_q1_revenue': q1_rev,
        '_q1_profit': q1_prof,
        '_q2_revenue': q2_revenue,
        '_q2_profit': q2_profit,
        '_q2_prior_profit': q2_prior_profit,
    }


def _fmt(val, divisor):
    """Format to readable number, returns None if invalid."""
    if val is None or pd.isna(val):
        return None
    return round(val / divisor, 2)


# ============================================================
# Step 5: 生成报告
# ============================================================
# ---- Concept/Industry enrichment from Eastmoney ----
def _get_secid(code):
    """Determine Eastmoney secid prefix. 1=SH, 0=SZ/BJ."""
    code = str(code).zfill(6)
    if code.startswith(('600', '601', '603', '605', '688', '689')):
        return f"1.{code}"
    return f"0.{code}"


def get_mkt_cap_tier(mkt_cap_yi):
    """Classify market cap into 5 tiers."""
    if mkt_cap_yi is None or mkt_cap_yi <= 0:
        return 'N/A'
    if mkt_cap_yi >= 1000:
        return '大盘(≥千亿)'
    elif mkt_cap_yi >= 500:
        return '中大盘(500-1000亿)'
    elif mkt_cap_yi >= 100:
        return '中盘(100-500亿)'
    elif mkt_cap_yi >= 50:
        return '中小盘(50-100亿)'
    else:
        return '小盘(<50亿)'


TierOrder = {'大盘(≥千亿)': 0, '中大盘(500-1000亿)': 1, '中盘(100-500亿)': 2, '中小盘(50-100亿)': 3, '小盘(<50亿)': 4, 'N/A': 5}


def enrich_concepts(df):
    """Fetch industry, region, concepts, and market cap from Eastmoney push2 API.
    Adds columns: industry_em, region, concepts, chains, mkt_cap_yi, mkt_cap_tier
    """
    codes = df['code'].unique()
    em_data = {}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

    for i, code in enumerate(codes):
        secid = _get_secid(code)
        try:
            r = requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": secid, "fields": "f12,f116,f127,f128,f129"},
                headers=headers, timeout=10
            )
            d = r.json().get('data', {})
            mkt_cap = d.get('f116', 0) or 0
            em_data[str(code).zfill(6)] = {
                'industry_em': d.get('f127', ''),
                'region': d.get('f128', ''),
                'concepts': d.get('f129', ''),
                'mkt_cap_yi': round(mkt_cap / 1e8, 1) if mkt_cap else 0,
            }
        except Exception:
            em_data[str(code).zfill(6)] = {'industry_em': '', 'region': '', 'concepts': '', 'mkt_cap_yi': 0}
        if (i + 1) % 20 == 0:
            _time.sleep(0.3)

    df = df.copy()
    df['industry_em'] = df['code'].apply(lambda c: em_data.get(str(c).zfill(6), {}).get('industry_em', ''))
    df['region'] = df['code'].apply(lambda c: em_data.get(str(c).zfill(6), {}).get('region', ''))
    df['concepts'] = df['code'].apply(lambda c: em_data.get(str(c).zfill(6), {}).get('concepts', ''))
    df['mkt_cap_yi'] = df['code'].apply(lambda c: em_data.get(str(c).zfill(6), {}).get('mkt_cap_yi', 0))
    df['mkt_cap_tier'] = df['mkt_cap_yi'].apply(get_mkt_cap_tier)

    CHAIN_MAP = {
        '新能源车': '新能源汽车', '锂电池概念': '新能源汽车', '电池技术': '新能源汽车',
        '储能概念': '新能源汽车', '小米汽车': '新能源汽车', '特斯拉概念': '新能源汽车',
        '充电桩': '新能源汽车', '自动驾驶': '新能源汽车', '汽车热管理': '新能源汽车',
        '人形机器人': '机器人', '机器人概念': '机器人', 'PEEK材料概念': '机器人',
        '半导体概念': '半导体', '国产芯片': '半导体', '存储芯片': '半导体',
        '消费电子概念': '消费电子', '智能穿戴': '消费电子', '无线耳机': '消费电子',
        'OLED': '消费电子', 'MiniLED': '消费电子', 'LED概念': '消费电子',
        '5G概念': '5G/通信', '通信技术': '5G/通信', '华为概念': '5G/通信',
        '商业航天': '航空航天', '军工': '航空航天',
        '氢能源': '新能源(风光氢)', '风能': '新能源(风光氢)', '绿色电力': '新能源(风光氢)',
        '太阳能': '新能源(风光氢)', '光伏概念': '新能源(风光氢)',
        '新材料': '新材料', '化学原料': '化工', '化学制品': '化工',
        '氟化工概念': '化工', '煤化工概念': '化工',
        '化学制药': '医药', '医疗器械': '医药', '医疗器械概念': '医药',
        '病毒防治': '医药', '创新药': '医药',
        '电子烟': '新型烟草', '一带一路': '基建/一带一路', '铁路基建': '基建/一带一路',
        '央国企改革': '国企改革', '智能电网': '电力设备', '电网设备': '电力设备',
        '节能环保': '环保', '环境治理': '环保', '垃圾分类': '环保',
        '3D打印': '高端制造', '专用设备': '高端制造', '通用设备': '高端制造',
        '苹果概念': '苹果产业链',
    }

    def _get_chains(row):
        chains = set()
        concepts = str(row.get('concepts', '')).split(',')
        for c in concepts:
            c = c.strip()
            if c in CHAIN_MAP:
                chains.add(CHAIN_MAP[c])
        if row.get('industry_em', '') in CHAIN_MAP:
            chains.add(CHAIN_MAP[row['industry_em']])
        if not chains:
            chains.add(row.get('industry_em', '其他'))
        return '/'.join(sorted(chains))

    df['chains'] = df.apply(_get_chains, axis=1)
    return df


def generate_report(df, report_path):
    """生成 Markdown 每日报告."""
    eprint("[5/5] 生成报告...")

    if len(df) == 0:
        report = f"""# 半年报追踪报告 — {today.strftime('%Y-%m-%d')}

## 今日概览
- **暂无半年报快报或预告披露**

数据更新于 {today.strftime('%Y-%m-%d %H:%M')}
"""
        with open(report_path, 'w') as f:
            f.write(report)
        eprint(f"  报告: {report_path} (空)")
        return report

    # Categorize by announcement date
    last_td_str = LAST_TRADE_DAY.strftime('%Y-%m-%d')
    today_str = today.strftime('%Y-%m-%d')
    week_start_str = (LAST_TRADE_DAY - timedelta(days=LAST_TRADE_DAY.weekday())).strftime('%Y-%m-%d')

    new_today = df[df['notice_date'] == today_str]
    new_last_trade = df[df['notice_date'] == last_td_str]
    new_this_week = df[(df['notice_date'] >= week_start_str) & (df['notice_date'] != last_td_str)]
    express_df = df[df['source'] == '快报']
    forecast_df = df[df['source'] == '预告']

    lines = []
    lines.append(f"# 半年报追踪报告 — {today.strftime('%Y-%m-%d')}")
    lines.append("")

    # Summary
    lines.append("## 📊 概览")
    lines.append("")
    lines.append(f"| 类别 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 上一交易日新增 ({last_td_str}) | {len(new_last_trade)} |")
    lines.append(f"| 今日新增 | {len(new_today)} |")
    lines.append(f"| 本周新增合计 | {len(new_this_week) + len(new_last_trade)} |")
    lines.append(f"| 全部快报已披露 | {len(express_df)} |")
    lines.append(f"| 全部预告已披露 | {len(forecast_df)} |")
    lines.append(f"| **合计** | **{len(df)}** |")
    # Market cap tier distribution
    tier_counts = df['mkt_cap_tier'].value_counts()
    for tier_name in ['大盘(≥千亿)', '中大盘(500-1000亿)', '中盘(100-500亿)', '中小盘(50-100亿)', '小盘(<50亿)']:
        cnt = tier_counts.get(tier_name, 0)
        if cnt > 0:
            lines.append(f"| 　├ {tier_name} | {cnt} 只 |")
    lines.append("")

    # --- Industry distribution ---
    lines.append("## 🏭 行业分布")
    lines.append("")
    lines.append("| 行业 | 数量 | 成分股 | 行业Q2景气度 |")
    lines.append("|------|------|--------|-------------|")

    by_ind = df.groupby('industry_em')
    for ind_name, group in sorted(by_ind, key=lambda x: -len(x[1])):
        yoys = [v for v in group['q2_prof_yoy_pct'].dropna() if v is not None]
        avg = sum(yoys)/len(yoys) if yoys else None
        if avg is not None:
            if avg > 50: mood = f"🔥 高景气 (均值↑{avg:.0f}%)"
            elif avg > 20: mood = f"✅ 景气 (均值↑{avg:.0f}%)"
            elif avg > 0: mood = f"📈 温和增长 (均值↑{avg:.0f}%)"
            else: mood = f"⚠️ 承压 (均值↓{abs(avg):.0f}%)"
        else:
            mood = "数据不足"
        names = '、'.join(group['name'].head(4).tolist())
        more = f" 等{len(group)}只" if len(group) > 4 else ""
        lines.append(f"| **{ind_name}** | {len(group)} | {names}{more} | {mood} |")
    lines.append("")

    # --- Chain distribution ---
    lines.append("## 🔗 产业链地图")
    lines.append("")
    lines.append("| 产业链 | 数量 | 代表股 | 链内龙头 |")
    lines.append("|--------|------|--------|----------|")

    chain_groups = defaultdict(list)
    for _, r in df.iterrows():
        for ch in str(r.get('chains', '')).split('/'):
            if ch.strip():
                chain_groups[ch.strip()].append(r)

    for chain_name, members in sorted(chain_groups.items(), key=lambda x: -len(x[1])):
        names = '、'.join([m['name'] for m in members[:3]])
        more = f" +{len(members)-3}" if len(members) > 3 else ""
        best = None
        for m in members:
            y = m.get('q2_prof_yoy_pct')
            if y is not None and (best is None or y > best[0]):
                best = (y, m['name'])
        leader = f"{best[1]} ↑{best[0]:.0f}%" if best else "-"
        lines.append(f"| **{chain_name}** | {len(members)} | {names}{more} | {leader} |")
    lines.append("")

    # --- Previous trading day new ---
    if len(new_last_trade) > 0:
        lines.append(f"## 🆕 上一交易日新增（{last_td_str}）")
        lines.append("")
        lines.extend(_render_table(new_last_trade))
        lines.append("")

    # --- Today (if running during trading hours) ---
    if len(new_today) > 0:
        lines.append("## 🆕 今日新增")
        lines.append("")
        lines.extend(_render_table(new_today))
        lines.append("")

    # --- This week's new ---
    if len(new_this_week) > 0:
        lines.append("## 📅 本周新增")
        lines.append("")
        lines.extend(_render_table(new_this_week))
        lines.append("")

    # --- Profit growth ranking (dynamic: full ranking when <=40, TOP/bottom split when >40) ---
    if len(df) > 0:
        yoy_ranked = df.dropna(subset=['q2_prof_yoy_pct']).copy()
        yoy_ranked = yoy_ranked[yoy_ranked['q2_profit_yi'].notna() & (yoy_ranked['q2_profit_yi'] > 0)]
        yoy_ranked = yoy_ranked.sort_values('q2_prof_yoy_pct', ascending=False)
        n_yoy = len(yoy_ranked)

        if n_yoy <= 40:
            # Small dataset: one unified ranking, highlight扭亏
            lines.append(f"## 📈 Q2 净利增速排名（全部 {n_yoy} 只，同比）")
            lines.append("")
            lines.extend(_render_table(yoy_ranked))
            # Only add扭亏 notes if any
            turn_profit_stocks = yoy_ranked[yoy_ranked['forecast_type'] == '扭亏']
            if len(turn_profit_stocks) > 0:
                lines.append("")
                lines.append("> ⚠️ **扭亏股**（标红）：因去年Q2亏损导致同比数字异常大/异常小，**看环比（QoQ）更准**。")
                for _, r in turn_profit_stocks.iterrows():
                    qoq = r.get('q2_prof_qoq_pct')
                    qoq_str = f"环比↑{qoq:.0f}%" if qoq and qoq > 0 else ""
                    lines.append(f"> - **{r['name']}**（{r['code']}）：去年Q2亏损{abs(r.get('_q2_prior_profit',0))/1e8:.2f}亿→今年盈利{r['q2_profit_yi']:.2f}亿 {qoq_str}")
            lines.append("")
        else:
            # Large dataset: TOP20 + decline warning
            profit_gainers = yoy_ranked.head(20)
            lines.append("## 📈 Q2 净利增速 TOP20（同比）")
            lines.append("")
            lines.extend(_render_table(profit_gainers))
            lines.append("")

            losers = yoy_ranked.sort_values('q2_prof_yoy_pct').head(20)
            real_decliners = losers[losers['q2_prof_yoy_pct'] < -30]
            if len(real_decliners) > 0:
                lines.append("## ⚠️ Q2 净利下滑预警")
                lines.append("")
                lines.extend(_render_table(real_decliners))
                lines.append("")

    # --- Tiered full list by market cap ---
    lines.append("## 📋 全部已披露（按市值分层）")
    lines.append("")

    # Sort by tier then Q2 profit
    df_tiered = df.copy()
    df_tiered['_tier_sort'] = df_tiered['mkt_cap_tier'].map(TierOrder).fillna(5)
    df_tiered = df_tiered.sort_values(['_tier_sort', 'q2_profit_yi'], ascending=[True, False])

    for tier_name in ['大盘(≥千亿)', '中大盘(500-1000亿)', '中盘(100-500亿)', '中小盘(50-100亿)', '小盘(<50亿)', 'N/A']:
        tier_df = df_tiered[df_tiered['mkt_cap_tier'] == tier_name]
        if len(tier_df) == 0:
            continue
        total_mkt_cap = tier_df['mkt_cap_yi'].sum()
        lines.append(f"### {tier_name}（{len(tier_df)}只，合计市值{total_mkt_cap:.0f}亿）")
        lines.append("")
        lines.append("| 代码 | 简称 | 市值(亿) | 行业 | 核心概念 | 预告 | H1净利(亿) | Q2净利(亿) | Q2同比 | Q2环比 |")
        lines.append("|------|------|---------|------|----------|------|-----------|-----------|--------|--------|")
        for _, r in tier_df.iterrows():
            ftype = r.get('forecast_type', '') or ''
            h1_prof = _fmt_cell(r.get('h1_profit_yi'))
            q2_prof = _fmt_cell(r.get('q2_profit_yi'))
            yoy = _fmt_pct(r.get('q2_prof_yoy_pct'))
            qoq = _fmt_pct(r.get('q2_prof_qoq_pct'))
            mkt_cap = f"{r['mkt_cap_yi']:.0f}" if r.get('mkt_cap_yi') else '-'
            ind = r.get('industry_em', '') or ''
            concepts_str = str(r.get('concepts', ''))
            concepts_3 = ', '.join(concepts_str.split(',')[:2]) if concepts_str else ''
            lines.append(f"| {r['code']} | **{r['name']}** | {mkt_cap} | {ind} | {concepts_3} | {ftype} | {h1_prof} | {q2_prof} | {yoy} | {qoq} |")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*数据更新于 {today.strftime('%Y-%m-%d %H:%M')}, 数据源: cjpy(天软) + akshare(东财)*")

    report = "\n".join(lines)
    with open(report_path, 'w') as f:
        f.write(report)
    eprint(f"  报告: {report_path} ({len(df)} 条)")
    return report


def _render_table(df):
    """Render a DataFrame subset as markdown table."""
    lines = []
    has_concepts = 'concepts' in df.columns
    if has_concepts:
        lines.append("| 代码 | 简称 | 行业 | 核心概念 | 类型 | 公告日 | 预告类型 | H1净利(亿) | Q2净利(亿) | Q2净利同比% | Q2净利环比% |")
        lines.append("|------|------|------|----------|------|--------|----------|-----------|-----------|------------|------------|")
    else:
        lines.append("| 代码 | 简称 | 类型 | 公告日 | 预告类型 | H1净利(亿) | Q1净利(亿) | Q2净利(亿) | Q2净利同比% | Q2净利环比% |")
        lines.append("|------|------|------|--------|----------|-----------|-----------|-----------|------------|------------|")

    for _, r in df.iterrows():
        ftype = r.get('forecast_type', '') or ''
        h1_prof = _fmt_cell(r.get('h1_profit_yi'))
        q1_prof = _fmt_cell(r.get('q1_profit_yi'))
        q2_prof = _fmt_cell(r.get('q2_profit_yi'))
        yoy = _fmt_pct(r.get('q2_prof_yoy_pct'))
        qoq = _fmt_pct(r.get('q2_prof_qoq_pct'))
        if has_concepts:
            ind = r.get('industry_em', '') or ''
            concepts_str = str(r.get('concepts', ''))
            concepts_3 = ', '.join(concepts_str.split(',')[:3]) if concepts_str else ''
            lines.append(f"| {r['code']} | {r['name']} | {ind} | {concepts_3} | {r['source']} | {r['notice_date']} | {ftype} | {h1_prof} | {q2_prof} | {yoy} | {qoq} |")
        else:
            lines.append(f"| {r['code']} | {r['name']} | {r['source']} | {r['notice_date']} | {ftype} | {h1_prof} | {q1_prof} | {q2_prof} | {yoy} | {qoq} |")

    return lines


def _fmt_cell(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return '-'
    return f"{val:.2f}"


def _fmt_pct(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return '-'
    arrow = '↑' if val > 0 else ('↓' if val < 0 else '→')
    return f"{arrow}{val:.1f}%"


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='半年报追踪')
    parser.add_argument('--out', default=None, help='CSV output path')
    parser.add_argument('--report', default=None, help='Report output path')
    parser.add_argument('--no-report', action='store_true', help='Skip report generation')
    parser.add_argument('--codes', nargs='*', help='Specific stock codes (for testing)')
    args = parser.parse_args()

    out_csv = args.out or OUT_CSV
    out_report = args.report or OUT_REPORT

    eprint(f"=== 半年报追踪 {today.strftime('%Y-%m-%d %H:%M')} ===")

    # 1. Forecasts
    forecasts = fetch_forecasts()

    # 2. Express reports
    express = fetch_express_reports()

    # 3. Collect unique codes
    all_codes = set()
    if len(forecasts) > 0:
        all_codes.update(forecasts['code'].tolist())
    if len(express) > 0:
        all_codes.update(express['code'].tolist())
    if args.codes:
        all_codes.update(args.codes)

    eprint(f"  合计: {len(all_codes)} 只相关股票需拉利润表")

    # 4. Income statements
    income = fetch_income_statements(list(all_codes))

    # 5. Calculate Q2 metrics
    result = calculate_q2(forecasts, express, income)

    # 6. Save CSV
    if len(result) > 0:
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        result.to_csv(out_csv, index=False)
        eprint(f"  CSV: {out_csv} ({len(result)} rows)")
    else:
        eprint("  无数据, 不写 CSV")

    # 6b. Enrich with concepts/industry from Eastmoney
    if len(result) > 0:
        eprint("  从东方财富获取行业/概念...")
        result = enrich_concepts(result)
        eprint(f"  概念数据获取完成")

    # 7. Report
    if not args.no_report:
        report = generate_report(result, out_report)
        print(report)  # stdout → fmdata 日志

    # Summary line for fmdata
    eprint(f"=== 完成: 快报{len(express)}只, 预告{len(forecasts)}只, 结果{len(result)}条 ===")
    return 0


if __name__ == '__main__':
    sys.exit(main())

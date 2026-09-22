#!/usr/bin/env python3
"""
半年报投资决策系统 - Semi-Annual Report Investment Decision System
=========================================================================
"""

import cjpy
import akshare as ak
import pandas as pd
import numpy as np
import json
import os
import sys
import re
import time as _time
import argparse
import requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

PERIOD_H1 = "20260630"
PERIOD_Q1 = "20260331"
PERIOD_H1_PRIOR = "20250630"
PERIOD_Q1_PRIOR = "20250331"
TODAY = datetime.now()
TODAY_STR = TODAY.strftime("%Y%m%d")
OUT_CSV = Path.home() / "fmdata/store/fundamentals/semiannual_investment.csv"
OUT_REPORT = Path.home() / "fmdata/store/fundamentals/semiannual_investment_report.md"
CJPY_BATCH = 500
EASTMONEY_BATCH = 100
EASTMONEY_DELAY = 0.15

WEIGHTS = {
    "net_profit_gap": 0.25,
    "price_confirm": 0.20,
    "q2_growth": 0.20,
    "pead": 0.10,
    "growth_accel": 0.10,
    "quality": 0.10,
    "analyst_revision": 0.05,
}

GAP_SCORE_MAP = {
    "大超预期": 10,
    "超预期": 7.5,
    "符合": 5,
    "低于": 2.5,
    "大幅低于": 0,
}

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)
    sys.stderr.flush()

def last_trading_day(ref_date=None):
    if ref_date is None:
        ref_date = TODAY
    d = ref_date - timedelta(days=0)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    try:
        r = requests.get(
            f"http://127.0.0.1:1934/calendar/is-trade-day?date={d.strftime('%Y%m%d')}",
            timeout=3
        )
        if r.status_code == 200:
            is_trade = r.json().get("is_trade_day", True)
            while not is_trade:
                d -= timedelta(days=1)
                while d.weekday() >= 5:
                    d -= timedelta(days=1)
                r = requests.get(
                    f"http://127.0.0.1:1934/calendar/is-trade-day?date={d.strftime('%Y%m%d')}",
                    timeout=3
                )
                is_trade = r.json().get("is_trade_day", True) if r.status_code == 200 else True
    except Exception:
        pass
    return d

LAST_TRADE_DAY = last_trading_day()

def to_cjpy_code(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605")):
        return f"SH{code}"
    elif code.startswith(("688", "689")):
        return f"SH{code}"
    elif code.startswith(("000", "001", "002", "003", "004", "300", "301")):
        return f"SZ{code}"
    elif code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "920")):
        return f"BJ{code}"
    return None

def to_raw_code(cjpy_code):
    if cjpy_code and len(cjpy_code) >= 8:
        return cjpy_code[2:]
    return str(cjpy_code).zfill(6)

def _growth_rate(current, prior):
    try:
        c = float(current) if current is not None else np.nan
        p = float(prior) if prior is not None else np.nan
        if np.isnan(c) or np.isnan(p) or p == 0:
            return np.nan
        return (c / p - 1) * 100
    except (ValueError, TypeError):
        return np.nan

def parse_forecast_range(change_text):
    if pd.isna(change_text) or not isinstance(change_text, str):
        return None, None, None
    text = change_text.replace(",", "").replace("，", "")
    
    def _parse_amount(amount_str):
        amount_str = amount_str.strip()
        try:
            if "亿元" in amount_str:
                return float(amount_str.replace("亿元", ""))
            elif "万元" in amount_str:
                return float(amount_str.replace("万元", "")) / 10000
            elif "元" in amount_str:
                return float(amount_str.replace("元", "")) / 1e8
            else:
                return float(amount_str) / 1e8
        except (ValueError, TypeError):
            return None
    
    # Range: 盈利:X万元至Y万元 or 净利润约X万元至Y万元
    range_match = re.search(
        r'(?:盈利|净利润)[：:]?\s*([\d.]+(?:亿|万)?元?)\s*[至～~-]\s*([\d.]+(?:亿|万)?元?)',
        text
    )
    if range_match:
        lower = _parse_amount(range_match.group(1))
        upper = _parse_amount(range_match.group(2))
        if lower is not None and upper is not None:
            return lower, upper, (lower + upper) / 2
    
    # Single value: 净利润约X万元
    single_match = re.search(
        r'(?:盈利|净利润)[约约]?[：:]?\s*([\d.]+(?:亿|万)?元?)',
        text
    )
    if single_match:
        val = _parse_amount(single_match.group(1))
        if val is not None:
            return val, val, val
    
    # Loss range: 亏损:X万元至Y万元
    loss_match = re.search(
        r'亏损[：:]?\s*([\d.]+(?:亿|万)?元?)\s*[至～~-]\s*([\d.]+(?:亿|万)?元?)',
        text
    )
    if loss_match:
        lower = _parse_amount(loss_match.group(1))
        upper = _parse_amount(loss_match.group(2))
        if lower is not None and upper is not None:
            return -upper, -lower, -(lower + upper) / 2
    
    # Single loss: 亏损约X万元
    loss_single = re.search(
        r'亏损[：:]?\s*([\d.]+(?:亿|万)?元?)',
        text
    )
    if loss_single:
        val = _parse_amount(loss_single.group(1))
        if val is not None:
            return -val, -val, -val
    
    return None, None, None

def get_mkt_cap_tier(mkt_cap_yi):
    if mkt_cap_yi is None or pd.isna(mkt_cap_yi) or mkt_cap_yi <= 0:
        return "N/A"
    if mkt_cap_yi >= 1000:
        return "大盘"
    elif mkt_cap_yi >= 500:
        return "中大盘"
    elif mkt_cap_yi >= 100:
        return "中盘"
    elif mkt_cap_yi >= 50:
        return "中小盘"
    else:
        return "小盘"

def secid_from_code(code):
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return f"1.{code}"
    else:
        return f"0.{code}"


# ══════════════════════════════════════════════════════════════════════
# Phase 1: Data Collection
# ══════════════════════════════════════════════════════════════════════

def fetch_forecasts():
    """Fetch H1 2026 performance forecasts from akshare, filter to 归母净利润."""
    eprint("[1/7] 拉取业绩预告...")
    try:
        df = ak.stock_yjyg_em(date="20260630")
        if df is None or df.empty:
            eprint("  WARNING: no forecast data returned")
            return pd.DataFrame()
    except Exception as e:
        eprint(f"  ERROR fetching forecasts: {e}")
        return pd.DataFrame()

    eprint(f"  Got {len(df)} raw forecast rows")

    # Filter to 归属于上市公司股东的净利润
    mask = df["预测指标"] == "归属于上市公司股东的净利润"
    df = df[mask].copy()
    eprint(f"  After filter to 归母净利润: {len(df)} rows")

    if df.empty:
        return df

    # Standardize code
    df["code"] = df["股票代码"].astype(str).str.zfill(6)
    df["name"] = df["股票简称"].astype(str)
    df["notice_date"] = pd.to_datetime(df["公告日期"], errors="coerce")
    df["forecast_type"] = df["预告类型"].astype(str)
    df["h1_forecast_yoy"] = pd.to_numeric(df["业绩变动幅度"], errors="coerce")
    df["h1_prior_profit_yi"] = pd.to_numeric(df["上年同期值"], errors="coerce") / 1e8
    df["change_text"] = df["业绩变动"].astype(str)

    # Parse forecast ranges
    ranges = df["change_text"].apply(parse_forecast_range)
    df["forecast_lower_yi"] = ranges.apply(lambda x: x[0])
    df["forecast_upper_yi"] = ranges.apply(lambda x: x[1])
    df["forecast_mid_yi"] = ranges.apply(lambda x: x[2])

    # Use 预测数值 as fallback (it"s in 元, convert to 亿元)
    raw_val = pd.to_numeric(df["预测数值"], errors="coerce") / 1e8
    mask_no_range = df["forecast_mid_yi"].isna()
    df.loc[mask_no_range, "forecast_mid_yi"] = raw_val[mask_no_range]
    df.loc[mask_no_range, "forecast_lower_yi"] = raw_val[mask_no_range] * 0.9
    df.loc[mask_no_range, "forecast_upper_yi"] = raw_val[mask_no_range] * 1.1

    df["source"] = "预告"
    cols = [
        "code", "name", "source", "notice_date", "forecast_type",
        "forecast_lower_yi", "forecast_upper_yi", "forecast_mid_yi",
        "h1_forecast_yoy", "h1_prior_profit_yi", "change_text"
    ]
    return df[cols].reset_index(drop=True)


def fetch_express_reports():
    """Fetch H1 2026 express reports from akshare."""
    eprint("[2/7] 拉取业绩快报...")
    try:
        df = ak.stock_yjkb_em(date="20260630")
        if df is None or df.empty:
            eprint("  WARNING: no express report data (may be too early)")
            return pd.DataFrame()
    except Exception as e:
        eprint(f"  WARNING: express reports not available: {e}")
        return pd.DataFrame()

    eprint(f"  Got {len(df)} express report rows")
    df["code"] = df["股票代码"].astype(str).str.zfill(6)
    df["name"] = df["股票简称"].astype(str)
    df["notice_date"] = pd.to_datetime(df["公告日期"], errors="coerce")

    # Revenue and profit in 亿元
    revenue_col = None
    profit_col = None
    for c in df.columns:
        if "营业" in c and "收入" in c:
            revenue_col = c
        if "净利润" in c:
            profit_col = c

    if revenue_col:
        df["h1_revenue_yi"] = pd.to_numeric(df[revenue_col], errors="coerce") / 1e8
    else:
        df["h1_revenue_yi"] = np.nan

    if profit_col:
        df["h1_profit_yi"] = pd.to_numeric(df[profit_col], errors="coerce") / 1e8
    else:
        df["h1_profit_yi"] = np.nan

    df["source"] = "快报"
    cols = ["code", "name", "source", "notice_date", "h1_revenue_yi", "h1_profit_yi"]
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)


def fetch_income_statements(codes):
    """Fetch consolidated income statements via cjpy for given codes.
    
    Returns dict: code -> {
        "h1_current": {revenue, profit},
        "q1_current": {revenue, profit},
        "h1_prior": {revenue, profit},
        "q1_prior": {revenue, profit},
    }
    """
    eprint("[3/7] 拉取合并利润表 (cjpy)...")
    if not codes:
        return {}

    cjpy_codes = []
    code_map = {}
    for c in codes:
        cj = to_cjpy_code(c)
        if cj:
            cjpy_codes.append(cj)
            code_map[cj] = c

    if not cjpy_codes:
        eprint("  No valid cjpy codes")
        return {}

    fin_data = {}
    for i in range(0, len(cjpy_codes), CJPY_BATCH):
        batch = cjpy_codes[i:i + CJPY_BATCH]
        eprint(f"  Batch {i // CJPY_BATCH + 1}/{(len(cjpy_codes) - 1) // CJPY_BATCH + 1}: {len(batch)} stocks")
        try:
            df = cjpy.get_table_data(batch, "合并利润表")
        except Exception as e:
            eprint(f"  ERROR cjpy batch: {e}")
            continue

        if df is None or df.empty:
            continue

        # Ensure 截止日 is string
        df["period"] = df["截止日"].astype(str)

        for cj_code, grp in df.groupby("CODE"):
            raw_code = code_map.get(cj_code, to_raw_code(cj_code))
            periods = {row["period"]: row for _, row in grp.iterrows()}

            entry = {"h1_current": None, "q1_current": None, "h1_prior": None, "q1_prior": None}

            for key, period_id in [
                ("h1_current", PERIOD_H1), ("q1_current", PERIOD_Q1),
                ("h1_prior", PERIOD_H1_PRIOR), ("q1_prior", PERIOD_Q1_PRIOR)
            ]:
                if period_id in periods:
                    row = periods[period_id]
                    rev = row.get("营业收入", None)
                    prof = row.get("归属于母公司所有者净利润", row.get("净利润", None))
                    try:
                        rev = float(rev) / 1e8 if rev is not None and not (isinstance(rev, float) and np.isnan(rev)) else None
                        prof = float(prof) / 1e8 if prof is not None and not (isinstance(prof, float) and np.isnan(prof)) else None
                    except (ValueError, TypeError):
                        rev, prof = None, None
                    entry[key] = {"revenue": rev, "profit": prof}

            fin_data[raw_code] = entry

    eprint(f"  Got income statements for {len(fin_data)} stocks")
    return fin_data


# ══════════════════════════════════════════════════════════════════════
# Phase 2: Q2 Calculation
# ══════════════════════════════════════════════════════════════════════

def calculate_q2(df, fin_data):
    """Decompose Q2 = H1 - Q1. Add Q2 columns to df."""
    eprint("[4/7] 计算Q2单季 (H1-Q1)...")

    for col in ["q2_revenue_yi", "q2_profit_yi", "q2_rev_yoy_pct", "q2_prof_yoy_pct",
                "q1_rev_yoy_pct", "q1_prof_yoy_pct", "q2_rev_qoq_pct", "q2_prof_qoq_pct",
                "h1_revenue_yi_actual", "h1_profit_yi_actual"]:
        df[col] = np.nan

    for idx, row in df.iterrows():
        code = row["code"]
        fd = fin_data.get(code, {})

        # --- Get H1 actual (from express report or income statement) ---
        h1_actual_rev = None
        h1_actual_prof = None

        if row.get("source") == "快报":
            h1_actual_rev = row.get("h1_revenue_yi")
            h1_actual_prof = row.get("h1_profit_yi")
        elif fd.get("h1_current"):
            h1_actual_rev = fd["h1_current"]["revenue"]
            h1_actual_prof = fd["h1_current"]["profit"]

        df.at[idx, "h1_revenue_yi_actual"] = h1_actual_rev
        df.at[idx, "h1_profit_yi_actual"] = h1_actual_prof

        # --- Get Q1 current ---
        q1_curr = fd.get("q1_current") or {}
        q1_curr_rev = q1_curr.get("revenue")
        q1_curr_prof = q1_curr.get("profit")

        # --- Get prior periods ---
        h1_prior = fd.get("h1_prior") or {}
        q1_prior = fd.get("q1_prior") or {}
        h1_prior_rev = h1_prior.get("revenue")
        h1_prior_prof = h1_prior.get("profit")
        q1_prior_rev = q1_prior.get("revenue")
        q1_prior_prof = q1_prior.get("profit")

        # --- Q2 = H1 - Q1 (use actual if available, fall back to forecast mid) ---
        if h1_actual_rev is not None and not (isinstance(h1_actual_rev, float) and np.isnan(h1_actual_rev)):
            q2_rev = h1_actual_rev - q1_curr_rev if q1_curr_rev is not None else None
        else:
            q2_rev = None

        if h1_actual_prof is not None and not (isinstance(h1_actual_prof, float) and np.isnan(h1_actual_prof)):
            q2_prof = h1_actual_prof - q1_curr_prof if q1_curr_prof is not None else None
        else:
            q2_prof = None

        df.at[idx, "q2_revenue_yi"] = q2_rev
        df.at[idx, "q2_profit_yi"] = q2_prof

        # --- Prior Q2 = H1_prior - Q1_prior ---
        q2_prior_rev = (
            h1_prior_rev - q1_prior_rev
            if h1_prior_rev is not None and q1_prior_rev is not None
            else None
        )
        q2_prior_prof = (
            h1_prior_prof - q1_prior_prof
            if h1_prior_prof is not None and q1_prior_prof is not None
            else None
        )

        # --- Growth rates ---
        df.at[idx, "q2_rev_yoy_pct"] = _growth_rate(q2_rev, q2_prior_rev)
        df.at[idx, "q2_prof_yoy_pct"] = _growth_rate(q2_prof, q2_prior_prof)
        df.at[idx, "q1_rev_yoy_pct"] = _growth_rate(q1_curr_rev, q1_prior_rev)
        df.at[idx, "q1_prof_yoy_pct"] = _growth_rate(q1_curr_prof, q1_prior_prof)
        df.at[idx, "q2_rev_qoq_pct"] = _growth_rate(q2_rev, q1_curr_rev)
        df.at[idx, "q2_prof_qoq_pct"] = _growth_rate(q2_prof, q1_curr_prof)

    # For forecasts: use forecast growth as q2 signal when no actual
    mask_no_actual = df["h1_profit_yi_actual"].isna()
    df.loc[mask_no_actual, "q2_profit_yi"] = df.loc[mask_no_actual, "forecast_mid_yi"]

    count_with_q2 = df["q2_profit_yi"].notna().sum()
    eprint(f"  Q2 profit calculated for {count_with_q2}/{len(df)} stocks")
    return df


# ══════════════════════════════════════════════════════════════════════
# Phase 3: Net Profit Gap Factor
# ══════════════════════════════════════════════════════════════════════

def compute_gap_factor(df):
    """Compute the net profit gap: actual Q2 vs forecast range."""
    eprint("[5/7] 计算净利润断层因子...")

    for col in ["gap_ratio", "gap_level", "gap_score", "gap_note"]:
        df[col] = np.nan
    df["gap_score"] = 5.0  # default neutral
    df["gap_level"] = "N/A"
    df["gap_note"] = ""

    gap_count = 0
    for idx, row in df.iterrows():
        actual = row.get("h1_profit_yi_actual")
        if actual is None or (isinstance(actual, float) and np.isnan(actual)):
            actual = row.get("q2_profit_yi")
        lower = row.get("forecast_lower_yi")
        upper = row.get("forecast_upper_yi")
        forecast_type = row.get("forecast_type", "")

        # Case 1: Has actual profit AND forecast range -> compute gap
        if (actual is not None and not (isinstance(actual, float) and np.isnan(actual))
                and upper is not None and not (isinstance(upper, float) and np.isnan(upper))
                and upper != 0):
            gap_count += 1
            ratio = (actual - upper) / abs(upper)

            if ratio > 0.2:
                level = "大超预期"
            elif ratio > 0:
                level = "超预期"
            elif actual >= (lower if lower is not None and not (isinstance(lower, float) and np.isnan(lower)) else upper):
                level = "符合"
            elif (lower is not None and not (isinstance(lower, float) and np.isnan(lower))
                  and actual >= lower * 0.8):
                level = "低于"
            else:
                level = "大幅低于"

            df.at[idx, "gap_ratio"] = round(ratio, 4)
            df.at[idx, "gap_level"] = level
            df.at[idx, "gap_score"] = GAP_SCORE_MAP.get(level, 5)
            df.at[idx, "gap_note"] = (
                f"实际{actual:.2f}亿 vs 预告上限{upper:.2f}亿, gap={ratio*100:.1f}%"
            )

        # Case 2: No actual yet, use forecast type as proxy
        elif forecast_type:
            pos_types = ["预增", "扭亏", "大增", "略增", "续盈"]
            neg_types = ["预减", "首亏", "续亏", "略减"]
            if forecast_type in pos_types:
                df.at[idx, "gap_level"] = "预告偏好"
                df.at[idx, "gap_score"] = 6.0
                df.at[idx, "gap_note"] = f"预告: {forecast_type}"
            elif forecast_type in neg_types:
                df.at[idx, "gap_level"] = "预告偏空"
                df.at[idx, "gap_score"] = 3.0
                df.at[idx, "gap_note"] = f"预告: {forecast_type}"
            else:
                df.at[idx, "gap_level"] = "预告待定"
                df.at[idx, "gap_note"] = f"预告: {forecast_type}"

    eprint(f"  Gap computed for {gap_count} stocks with both actual and forecast")
    return df


# ══════════════════════════════════════════════════════════════════════
# Phase 4: Eastmoney Enrichment (Industry / Concepts / Market Cap)
# ══════════════════════════════════════════════════════════════════════

def enrich_eastmoney(df):
    """Fetch industry, concepts, and market cap from Eastmoney push2 API."""
    eprint("[6/7] 拉取东方财富行业/概念/市值...")

    for col in ["mkt_cap_yi", "industry", "concepts", "mkt_cap_tier"]:
        df[col] = np.nan
    df["industry"] = ""
    df["concepts"] = ""

    codes = df["code"].unique().tolist()
    batch_size = EASTMONEY_BATCH

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        eprint(f"  Eastmoney batch {i // batch_size + 1}/{(len(codes) - 1) // batch_size + 1}: {len(batch)} stocks")

        for code in batch:
            try:
                secid = secid_from_code(code)
                url = (
                    f"https://push2.eastmoney.com/api/qt/stock/get"
                    f"?secid={secid}&fields=f12,f116,f127,f128,f129"
                )
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data.get("data"):
                    d = data["data"]
                    mkt_cap = d.get("f116")
                    if mkt_cap and mkt_cap > 0:
                        mask = df["code"] == code
                        df.loc[mask, "mkt_cap_yi"] = mkt_cap / 1e8  # yuan -> yi
                        df.loc[mask, "industry"] = d.get("f127", "")
                        df.loc[mask, "concepts"] = d.get("f129", "")
                        df.loc[mask, "mkt_cap_tier"] = get_mkt_cap_tier(mkt_cap / 1e8)
            except Exception as e:
                pass

            _time.sleep(EASTMONEY_DELAY)

    enriched = df["mkt_cap_yi"].notna().sum()
    eprint(f"  Eastmoney data enriched for {enriched}/{len(df)} stocks")
    return df


# ══════════════════════════════════════════════════════════════════════
# Phase 5: Price Data & Event Study
# ══════════════════════════════════════════════════════════════════════

def fetch_price_data(codes, start_date="20260401", end_date=None, include_benchmark=True):
    """Batch fetch OHLCV daily data from fmdata for all codes.
    
    Returns dict: code -> DataFrame with columns [trade_date, open, high, low, close, pre_close, pct_chg, vol, amount].
    Caches results to reduce API calls.
    """
    if end_date is None:
        end_date = TODAY_STR

    eprint(f"     Fetching price data for {len(codes)} stocks ({start_date}-{end_date})...")
    price_cache = {}
    session = requests.Session()

    for i, code in enumerate(codes):
        if (i + 1) % 200 == 0:
            eprint(f"     Price fetch: {i + 1}/{len(codes)}")

        try:
            url = f"http://127.0.0.1:1934/market/stock-daily?code={code}&start={start_date}&end={end_date}"
            r = session.get(url, timeout=30)
            r.raise_for_status()
            js = r.json()

            if not js.get("data"):
                continue

            rows = []
            for item in js["data"]:
                rows.append({
                    "trade_date": int(item.get("trade_date", 0)),
                    "open": float(item.get("open", np.nan)),
                    "high": float(item.get("high", np.nan)),
                    "low": float(item.get("low", np.nan)),
                    "close": float(item.get("close", np.nan)),
                    "pre_close": float(item.get("pre_close", np.nan)),
                    "pct_chg": float(item.get("pct_chg", np.nan)),
                    "vol": float(item.get("vol", np.nan)),
                    "amount": float(item.get("amount", np.nan)),
                })

            price_cache[code] = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        except Exception as e:
            continue

    eprint(f"     Price data fetched for {len(price_cache)}/{len(codes)} stocks")

    # Fetch benchmark (沪深300)
    if include_benchmark:
        try:
            url = f"http://127.0.0.1:1934/market/stock-daily?code=000300&start={start_date}&end={end_date}"
            r = session.get(url, timeout=30)
            r.raise_for_status()
            js = r.json()
            if js.get("data"):
                rows = []
                for item in js["data"]:
                    rows.append({
                        "trade_date": int(item.get("trade_date", 0)),
                        "close": float(item.get("close", np.nan)),
                        "pct_chg": float(item.get("pct_chg", np.nan)),
                    })
                price_cache["__benchmark__"] = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        except Exception:
            pass

    session.close()
    return price_cache


def find_event_trading_day(notice_date, price_df):
    """Find the first trading day on or after notice_date in price data.
    
    Returns (trade_date_int, index_in_price_df) or (None, None).
    """
    if price_df is None or price_df.empty or pd.isna(notice_date):
        return None, None

    notice_dt = pd.Timestamp(notice_date)
    notice_int = int(notice_dt.strftime("%Y%m%d"))

    mask = price_df["trade_date"] >= notice_int
    if not mask.any():
        return None, None

    idx = mask.idxmax()
    return int(price_df.at[idx, "trade_date"]), idx


def compute_event_metrics(notice_date, price_df, benchmark_df=None):
    """Event study for a single stock announcement.
    
    Returns dict with event metrics, or empty dict if not enough data.
    """
    if price_df is None or price_df.empty:
        return {}

    t0_date, t0_idx = find_event_trading_day(notice_date, price_df)
    if t0_date is None:
        return {}

    # Check if event day is today or in the future
    today_int = int(TODAY_STR)
    if t0_date > today_int:
        return {
            "open_gap_pct": np.nan,
            "intraday_pct": np.nan,
            "day_return_pct": np.nan,
            "vol_ratio": np.nan,
            "car_3d": np.nan,
            "car_5d": np.nan,
            "car_10d": np.nan,
            "car_3d_abnormal": np.nan,
            "car_5d_abnormal": np.nan,
            "gap_filled": np.nan,
            "event_days_available": 0,
        }

    # Need at least T-1 for event study
    if t0_idx < 1:
        return {}

    # T-1 close
    close_before = price_df.at[t0_idx - 1, "close"]
    open_t0 = price_df.at[t0_idx, "open"]
    close_t0 = price_df.at[t0_idx, "close"]
    vol_t0 = price_df.at[t0_idx, "vol"]
    pct_chg_t0 = price_df.at[t0_idx, "pct_chg"]

    # T=0 metrics
    open_gap_pct = (open_t0 - close_before) / close_before * 100 if close_before and close_before != 0 else np.nan
    intraday_pct = (close_t0 - open_t0) / open_t0 * 100 if open_t0 and open_t0 != 0 else np.nan
    day_return_pct = pct_chg_t0 if not np.isnan(pct_chg_t0) else (
        (close_t0 - close_before) / close_before * 100 if close_before and close_before != 0 else np.nan
    )

    # Volume ratio (T / avg of prior 20 days)
    if t0_idx >= 21:
        avg_vol = price_df.iloc[t0_idx - 20:t0_idx]["vol"].mean()
        vol_ratio = vol_t0 / avg_vol if avg_vol and avg_vol > 0 else np.nan
    elif t0_idx >= 2:
        avg_vol = price_df.iloc[0:t0_idx]["vol"].mean()
        vol_ratio = vol_t0 / avg_vol if avg_vol and avg_vol > 0 else np.nan
    else:
        vol_ratio = np.nan

    # Post-announcement drift
    def _cum_return(start_idx, n_days):
        """Cumulative return from start_idx+1 to start_idx+n_days, relative to close at start_idx."""
        end_idx = min(start_idx + n_days, len(price_df) - 1)
        if end_idx <= start_idx:
            return np.nan
        start_close = price_df.at[start_idx, "close"]
        end_close = price_df.at[end_idx, "close"]
        if start_close and start_close != 0:
            return (end_close - start_close) / start_close * 100
        return np.nan

    car_3d = _cum_return(t0_idx, 3)
    car_5d = _cum_return(t0_idx, 5)
    car_10d = _cum_return(t0_idx, 10)

    # Abnormal returns (vs benchmark)
    car_3d_abnormal = np.nan
    car_5d_abnormal = np.nan
    if benchmark_df is not None and not benchmark_df.empty:
        bm_t0_date, bm_t0_idx = find_event_trading_day(notice_date, benchmark_df)
        if bm_t0_idx is not None:
            def _bm_cum(start_idx, n_days):
                end_idx = min(start_idx + n_days, len(benchmark_df) - 1)
                if end_idx <= start_idx:
                    return np.nan
                sc = benchmark_df.at[start_idx, "close"]
                ec = benchmark_df.at[end_idx, "close"]
                return (ec - sc) / sc * 100 if sc and sc != 0 else np.nan

            bm_3d = _bm_cum(bm_t0_idx, 3)
            bm_5d = _bm_cum(bm_t0_idx, 5)
            car_3d_abnormal = car_3d - bm_3d if not np.isnan(car_3d) and not np.isnan(bm_3d) else np.nan
            car_5d_abnormal = car_5d - bm_5d if not np.isnan(car_5d) and not np.isnan(bm_5d) else np.nan

    # Gap fill detection
    gap_filled = 0
    if not np.isnan(open_gap_pct) and open_gap_pct > 0:
        for j in range(t0_idx + 1, min(t0_idx + 6, len(price_df))):
            if price_df.at[j, "low"] < close_before:
                gap_filled = 1
                break
    elif not np.isnan(open_gap_pct) and open_gap_pct < 0:
        for j in range(t0_idx + 1, min(t0_idx + 6, len(price_df))):
            if price_df.at[j, "high"] > close_before:
                gap_filled = 1
                break

    # Days available after event
    event_days_available = len(price_df) - t0_idx - 1

    return {
        "open_gap_pct": round(open_gap_pct, 2) if not np.isnan(open_gap_pct) else np.nan,
        "intraday_pct": round(intraday_pct, 2) if not np.isnan(intraday_pct) else np.nan,
        "day_return_pct": round(day_return_pct, 2) if not np.isnan(day_return_pct) else np.nan,
        "vol_ratio": round(vol_ratio, 2) if not np.isnan(vol_ratio) else np.nan,
        "car_3d": round(car_3d, 2) if not np.isnan(car_3d) else np.nan,
        "car_5d": round(car_5d, 2) if not np.isnan(car_5d) else np.nan,
        "car_10d": round(car_10d, 2) if not np.isnan(car_10d) else np.nan,
        "car_3d_abnormal": round(car_3d_abnormal, 2) if not np.isnan(car_3d_abnormal) else np.nan,
        "car_5d_abnormal": round(car_5d_abnormal, 2) if not np.isnan(car_5d_abnormal) else np.nan,
        "gap_filled": gap_filled,
        "event_days_available": event_days_available,
    }


def compute_all_event_metrics(df, price_cache):
    """Compute event study metrics for all stocks."""
    eprint("[7/7] 计算事件研究指标...")

    benchmark_df = price_cache.get("__benchmark__")

    event_cols = [
        "open_gap_pct", "intraday_pct", "day_return_pct", "vol_ratio",
        "car_3d", "car_5d", "car_10d",
        "car_3d_abnormal", "car_5d_abnormal",
        "gap_filled", "event_days_available",
    ]
    for col in event_cols:
        df[col] = np.nan

    event_count = 0
    for idx, row in df.iterrows():
        code = row["code"]
        notice = row.get("notice_date")
        if pd.isna(notice):
            continue

        price_df = price_cache.get(code)
        if price_df is None or price_df.empty:
            continue

        metrics = compute_event_metrics(notice, price_df, benchmark_df)
        for k, v in metrics.items():
            df.at[idx, k] = v

        if metrics:
            event_count += 1

    eprint(f"  Event metrics computed for {event_count}/{len(df)} stocks")
    return df


# ══════════════════════════════════════════════════════════════════════
# Phase 6: Composite Scoring
# ══════════════════════════════════════════════════════════════════════

def _percentile_score(series, invert=False):
    """Convert a numeric series to 0-10 scores based on percentile rank.
    
    Higher values get higher scores by default. Set invert=True for
    metrics where lower is better.
    """
    valid = series.dropna()
    if len(valid) < 3:
        return pd.Series(5.0, index=series.index)

    ranks = valid.rank(pct=True)
    if invert:
        ranks = 1 - ranks
    scores = ranks * 10

    result = pd.Series(5.0, index=series.index)
    result[valid.index] = scores.values
    return result


def compute_composite_score(df):
    """Compute weighted composite score from all factors."""
    eprint("     Computing composite scores...")

    # 1. Q2 Growth score (0-10 from percentile of q2_prof_yoy_pct)
    df["q2_growth_score"] = _percentile_score(df["q2_prof_yoy_pct"])

    # 2. Gap score (already 0-10 from mapping)
    df["gap_score_norm"] = df["gap_score"].fillna(5)

    # 3. Price confirmation score
    # Composite: open_gap * 0.3 + intraday * 0.3 + vol_ratio_norm * 0.4
    vol_ratio_score = _percentile_score(df["vol_ratio"])
    df["price_confirm_raw"] = (
        df["open_gap_pct"].fillna(0) * 0.3
        + df["intraday_pct"].fillna(0) * 0.3
        + vol_ratio_score * 0.4
    )
    df["price_confirm_score"] = _percentile_score(df["price_confirm_raw"])
    # For stocks without event data, use neutral score
    no_event = df["open_gap_pct"].isna()
    df.loc[no_event, "price_confirm_score"] = 5.0

    # 4. PEAD score (0-10 from car_5d_abnormal percentile)
    df["pead_score"] = _percentile_score(df["car_5d_abnormal"])
    no_car = df["car_5d_abnormal"].isna()
    df.loc[no_car, "pead_score"] = 5.0

    # 5. Growth acceleration (Q2 YoY - Q1 YoY)
    df["growth_accel"] = df["q2_prof_yoy_pct"] - df["q1_prof_yoy_pct"]
    df["growth_accel_score"] = _percentile_score(df["growth_accel"])

    # 6. Quality score: placeholder from fundamentals if available
    # Try to fetch from fmdata
    df["quality_score"] = 5.0
    try:
        r = requests.get(
            f"http://127.0.0.1:1934/market/fundamentals?period=20260331",
            timeout=30
        )
        if r.status_code == 200:
            fund = r.json()
            if fund.get("data"):
                fund_df = pd.DataFrame(fund["data"])
                fund_df["code"] = fund_df["ts_code"].str[:6]
                # Use ROE as quality proxy
                for _, frow in fund_df.iterrows():
                    code = frow.get("code")
                    roe = frow.get("roe")
                    if code and roe is not None:
                        mask = df["code"] == code
                        if mask.any():
                            df.loc[mask, "quality_score"] = max(0, min(10, (float(roe) * 100 + 10) * 0.5))
    except Exception:
        pass

    # 7. Analyst revision (placeholder - use 5 neutral)
    df["analyst_revision_score"] = 5.0

    # ── Weighted total ──
    df["total_score"] = (
        df["gap_score_norm"] * WEIGHTS["net_profit_gap"]
        + df["price_confirm_score"] * WEIGHTS["price_confirm"]
        + df["q2_growth_score"] * WEIGHTS["q2_growth"]
        + df["pead_score"] * WEIGHTS["pead"]
        + df["growth_accel_score"] * WEIGHTS["growth_accel"]
        + df["quality_score"] * WEIGHTS["quality"]
        + df["analyst_revision_score"] * WEIGHTS["analyst_revision"]
    )

    # Rank
    df["score_rank"] = df["total_score"].rank(ascending=False, method="min").astype(int)

    # Round scores
    for c in ["q2_growth_score", "price_confirm_score", "pead_score",
              "growth_accel_score", "quality_score", "total_score"]:
        df[c] = df[c].round(2)

    eprint(f"     Scores computed. Top score: {df['total_score'].max():.2f}, "
           f"median: {df['total_score'].median():.2f}")
    return df


# ══════════════════════════════════════════════════════════════════════
# Phase 7: Report Generation
# ══════════════════════════════════════════════════════════════════════

def generate_report(df):
    """Generate markdown investment decision report."""
    eprint("     生成报告...")
    
    lines = []
    report_date = TODAY.strftime("%Y-%m-%d")
    last_td = LAST_TRADE_DAY.strftime("%Y-%m-%d") if LAST_TRADE_DAY else "N/A"
    
    # ── Header ──
    lines.append(f"# 半年报投资决策系统 — {report_date} (最后交易日: {last_td})")
    lines.append("")
    lines.append(f"> 自动生成于 {TODAY.strftime('%Y-%m-%d %H:%M')} | "
                 f"覆盖 {len(df)} 只个股 | "
                 f"因子权重: 断层{WEIGHTS['net_profit_gap']:.0%} "
                 f"价格{WEIGHTS['price_confirm']:.0%} "
                 f"增速{WEIGHTS['q2_growth']:.0%} "
                 f"PEAD{WEIGHTS['pead']:.0%} "
                 f"加速度{WEIGHTS['growth_accel']:.0%} "
                 f"质量{WEIGHTS['quality']:.0%}")
    lines.append("")
    
    # ── Section 1: Market Overview ──
    lines.append("## 一、市场全景")
    lines.append("")
    source_counts = df["source"].value_counts()
    lines.append(f"- **预告**: {source_counts.get('预告', 0)} 只")
    lines.append(f"- **快报**: {source_counts.get('快报', 0)} 只")
    
    date_range = df["notice_date"].dropna()
    if len(date_range) > 0:
        lines.append(f"- **公告日期范围**: {date_range.min().strftime('%Y-%m-%d')} ~ {date_range.max().strftime('%Y-%m-%d')}")
    
    # Avg scores by market cap tier
    lines.append("")
    lines.append("### 市值分层平均得分")
    lines.append("")
    lines.append("| 市值层级 | 数量 | 平均得分 | 最高分 |")
    lines.append("|----------|------|----------|--------|")
    tier_order = ["大盘", "中大盘", "中盘", "中小盘", "小盘", "N/A"]
    for tier in tier_order:
        subset = df[df["mkt_cap_tier"] == tier]
        if len(subset) > 0:
            avg = subset["total_score"].mean()
            mx = subset["total_score"].max()
            lines.append(f"| {tier} | {len(subset)} | {avg:.2f} | {mx:.2f} |")
    lines.append("")
    
    # ── Section 2: Net Profit Gap (断层) ──
    lines.append("## 二、净利润断层榜")
    lines.append("")
    gap_levels = ["大超预期", "超预期"]
    gap_df = df[df["gap_level"].isin(gap_levels)].sort_values("gap_ratio", ascending=False)
    
    if len(gap_df) > 0:
        lines.append(f"### 超预期个股 ({len(gap_df)} 只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 预告类型 | 预告上限(亿) | 实际H1(亿) | 断层率 | 等级 |")
        lines.append("|------|------|----------|-------------|-----------|--------|------|")
        for _, row in gap_df.head(30).iterrows():
            code = row.get("code", "")
            name = row.get("name", "")
            ftype = row.get("forecast_type", "")
            upper = row.get("forecast_upper_yi")
            actual = row.get("h1_profit_yi_actual")
            ratio = row.get("gap_ratio")
            level = row.get("gap_level", "")
            upper_str = f"{upper:.2f}" if not (isinstance(upper, float) and np.isnan(upper)) else "N/A"
            actual_str = f"{actual:.2f}" if not (isinstance(actual, float) and np.isnan(actual)) else "N/A"
            ratio_str = f"{ratio*100:.1f}%" if not (isinstance(ratio, float) and np.isnan(ratio)) else "N/A"
            lines.append(f"| {code} | {name} | {ftype} | {upper_str} | {actual_str} | {ratio_str} | {level} |")
        lines.append("")
    
    neg_levels = ["低于", "大幅低于"]
    neg_df = df[df["gap_level"].isin(neg_levels)]
    if len(neg_df) > 0:
        lines.append(f"### 低于预期 ({len(neg_df)} 只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 预告下限(亿) | 实际H1(亿) | 断层率 |")
        lines.append("|------|------|-------------|-----------|--------|")
        for _, row in neg_df.head(15).iterrows():
            lower = row.get("forecast_lower_yi")
            actual = row.get("h1_profit_yi_actual")
            lower_str = f"{lower:.2f}" if not (isinstance(lower, float) and np.isnan(lower)) else "N/A"
            actual_str = f"{actual:.2f}" if not (isinstance(actual, float) and np.isnan(actual)) else "N/A"
            ratio = row.get("gap_ratio")
            ratio_str = f"{ratio*100:.1f}%" if not (isinstance(ratio, float) and np.isnan(ratio)) else "N/A"
            lines.append(f"| {row.get('code', '')} | {row.get('name', '')} | {lower_str} | {actual_str} | {ratio_str} |")
        lines.append("")
    
    # ── Section 3: Price Confirmation ──
    lines.append("## 三、价格确认信号")
    lines.append("")
    
    # Strongest positive price reaction
    price_pos = df[df["open_gap_pct"].notna() & (df["open_gap_pct"] > 0)].sort_values("open_gap_pct", ascending=False)
    if len(price_pos) > 0:
        lines.append(f"### 公告日高开个股 ({len(price_pos)} 只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 公告日 | 开盘跳空% | 日内% | 日收益% | 量比 | CAR_5d |")
        lines.append("|------|------|--------|----------|-------|---------|------|--------|")
        for _, row in price_pos.head(20).iterrows():
            notice = row.get("notice_date")
            notice_str = notice.strftime("%m-%d") if not pd.isna(notice) else "N/A"
            lines.append(
                f"| {row.get('code', '')} | {row.get('name', '')} | {notice_str} | "
                f"{row.get('open_gap_pct', 'N/A')} | {row.get('intraday_pct', 'N/A')} | "
                f"{row.get('day_return_pct', 'N/A')} | {row.get('vol_ratio', 'N/A')} | "
                f"{row.get('car_5d', 'N/A')} |"
            )
        lines.append("")
    
    # ── Section 4: Top 20 Composite ──
    lines.append("## 四、综合评分 TOP20")
    lines.append("")
    top20 = df.nlargest(20, "total_score")
    lines.append("| 排名 | 代码 | 名称 | 总分 | 断层 | 价格 | Q2增速 | 市值层级 | 关键信号 |")
    lines.append("|------|------|------|------|------|------|--------|----------|----------|")
    for _, row in top20.iterrows():
        key_signal = row.get("gap_note", "")
        if len(str(key_signal)) > 30:
            key_signal = str(key_signal)[:27] + "..."
        lines.append(
            f"| {int(row.get('score_rank', 0))} | {row.get('code', '')} | {row.get('name', '')} | "
            f"{row.get('total_score', 0):.2f} | {row.get('gap_score', 0):.1f} | "
            f"{row.get('price_confirm_score', 0):.1f} | {row.get('q2_growth_score', 0):.1f} | "
            f"{row.get('mkt_cap_tier', 'N/A')} | {key_signal} |"
        )
    lines.append("")
    
    # ── Section 5: Risk Warnings ──
    lines.append("## 五、风险预警")
    lines.append("")
    risk_mask = (
        df["gap_level"].isin(["低于", "大幅低于"])
        | ((df["open_gap_pct"] < -3) & df["open_gap_pct"].notna())
        | (df["total_score"] < df["total_score"].quantile(0.1))
    )
    risk_df = df[risk_mask].sort_values("total_score")
    
    if len(risk_df) > 0:
        lines.append(f"### 需要关注的个股 ({len(risk_df)} 只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 风险信号 | 断层 | 总分 |")
        lines.append("|------|------|----------|------|------|")
        for _, row in risk_df.head(30).iterrows():
            risks = []
            if row.get("gap_level") in ["低于", "大幅低于"]:
                risks.append(f"业绩{row['gap_level']}")
            if not pd.isna(row.get("open_gap_pct")) and row.get("open_gap_pct", 0) < -3:
                risks.append(f"跳空低开{row['open_gap_pct']:.1f}%")
            lines.append(
                f"| {row.get('code', '')} | {row.get('name', '')} | "
                f"{', '.join(risks)} | {row.get('gap_level', 'N/A')} | "
                f"{row.get('total_score', 0):.2f} |"
            )
        lines.append("")
    else:
        lines.append("暂无显著风险信号。")
        lines.append("")
    
    # ── Section 6: Full Detail by Market Cap Tier ──
    lines.append("## 六、全量明细（按市值分层）")
    lines.append("")
    
    for tier in tier_order:
        tier_df = df[df["mkt_cap_tier"] == tier].sort_values("total_score", ascending=False)
        if len(tier_df) == 0:
            continue
        
        lines.append(f"### {tier} ({len(tier_df)} 只)")
        lines.append("")
        lines.append("| 代码 | 名称 | 来源 | 公告日 | 预告类型 | Q2利润(亿) | Q2 YoY% | 断层 | 开盘跳空% | 总分 |")
        lines.append("|------|------|------|--------|----------|-----------|---------|------|----------|------|")
        for _, row in tier_df.head(50).iterrows():
            notice = row.get("notice_date")
            notice_str = notice.strftime("%m-%d") if not pd.isna(notice) else "N/A"
            q2_prof = row.get("q2_profit_yi")
            q2_prof_str = f"{q2_prof:.2f}" if not (isinstance(q2_prof, float) and np.isnan(q2_prof)) else "N/A"
            q2_yoy = row.get("q2_prof_yoy_pct")
            q2_yoy_str = f"{q2_yoy:.1f}%" if not (isinstance(q2_yoy, float) and np.isnan(q2_yoy)) else "N/A"
            open_gap = row.get("open_gap_pct")
            open_gap_str = f"{open_gap:.1f}%" if not (isinstance(open_gap, float) and np.isnan(open_gap)) else "N/A"
            lines.append(
                f"| {row.get('code', '')} | {row.get('name', '')} | {row.get('source', '')} | "
                f"{notice_str} | {row.get('forecast_type', '')} | {q2_prof_str} | {q2_yoy_str} | "
                f"{row.get('gap_level', 'N/A')} | {open_gap_str} | {row.get('total_score', 0):.2f} |"
            )
            if len(tier_df) > 50 and _ == tier_df.index[49]:
                lines.append(f"| ... | (剩余 {len(tier_df) - 50} 只见CSV) | ... | ... | ... | ... | ... | ... | ... | ... |")
        lines.append("")
    
    # ── Section 7: Strategy Reference ──
    lines.append("## 七、投资策略参考")
    lines.append("")
    
    # Score distribution
    lines.append(f"### 评分分布")
    lines.append(f"- 平均分: {df['total_score'].mean():.2f}")
    lines.append(f"- 中位数: {df['total_score'].median():.2f}")
    lines.append(f"- 标准差: {df['total_score'].std():.2f}")
    lines.append(f"- 90分位: {df['total_score'].quantile(0.9):.2f}")
    lines.append(f"- 10分位: {df['total_score'].quantile(0.1):.2f}")
    lines.append("")
    
    # Industry insights
    has_industry = df[df["industry"].notna() & (df["industry"] != "")]
    if len(has_industry) > 10:
        industry_scores = has_industry.groupby("industry").agg(
            count=("total_score", "count"),
            avg_score=("total_score", "mean"),
            pos_gap=("gap_level", lambda x: (x.isin(["大超预期", "超预期"])).sum()),
        ).sort_values("avg_score", ascending=False)
        
        lines.append("### 行业信号（按平均分排序）")
        lines.append("")
        lines.append("| 行业 | 数量 | 平均分 | 超预期数 |")
        lines.append("|------|------|--------|----------|")
        for ind, row in industry_scores.head(10).iterrows():
            lines.append(f"| {ind} | {int(row['count'])} | {row['avg_score']:.2f} | {int(row['pos_gap'])} |")
        lines.append("")
    
    # Actionable observations
    lines.append("### 可操作观察")
    lines.append("")
    
    super_gap = df[df["gap_level"] == "大超预期"]
    if len(super_gap) > 0:
        names = ", ".join(f"{r['name']}({r['code']})" for _, r in super_gap.head(5).iterrows())
        lines.append(f"1. **大超预期**: {len(super_gap)} 只个股实际H1利润大幅超越预告上限: {names}")
    
    strong_price = df[(df["open_gap_pct"] > 5) & df["open_gap_pct"].notna()]
    if len(strong_price) > 0:
        names = ", ".join(f"{r['name']}({r['code']})" for _, r in strong_price.head(5).iterrows())
        lines.append(f"2. **公告日强势跳空** (>5%): {len(strong_price)} 只: {names}")
    
    strong_car = df[(df["car_5d"] > 10) & df["car_5d"].notna()]
    if len(strong_car) > 0:
        names = ", ".join(f"{r['name']}({r['code']})" for _, r in strong_car.head(5).iterrows())
        lines.append(f"3. **公告后持续强势** (CAR_5d>10%): {len(strong_car)} 只: {names}")
    
    both = df[
        (df["gap_level"].isin(["大超预期", "超预期"]))
        & (df["open_gap_pct"] > 2)
        & df["open_gap_pct"].notna()
    ]
    if len(both) > 0:
        names = ", ".join(f"{r['name']}({r['code']})" for _, r in both.head(5).iterrows())
        lines.append(f"4. **业绩+价格双确认**: {len(both)} 只超预期且公告日高开>2%: {names}")
    else:
        lines.append("4. **业绩+价格双确认**: 暂无满足条件的个股（可能公告日尚未到来）")
    
    lines.append("")
    lines.append("---")
    lines.append(f"*免责声明: 本报告为量化系统自动生成，不构成投资建议。数据来源: akshare, cjpy天软, fmdata, 东方财富。*")
    lines.append("")
    
    return "\n".join(lines)


def save_csv(df, path=None):
    """Save results to CSV."""
    if path is None:
        path = OUT_CSV
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Select and order columns for CSV
    csv_cols = [
        "code", "name", "source", "notice_date", "forecast_type",
        "forecast_lower_yi", "forecast_upper_yi", "forecast_mid_yi",
        "h1_profit_yi_actual", "q2_profit_yi",
        "q2_rev_yoy_pct", "q2_prof_yoy_pct", "q1_prof_yoy_pct",
        "q2_prof_qoq_pct",
        "gap_ratio", "gap_level", "gap_score", "gap_note",
        "open_gap_pct", "intraday_pct", "day_return_pct", "vol_ratio",
        "car_3d", "car_5d", "car_10d",
        "car_3d_abnormal", "car_5d_abnormal", "gap_filled",
        "event_days_available",
        "mkt_cap_yi", "mkt_cap_tier", "industry", "concepts",
        "q2_growth_score", "price_confirm_score", "pead_score",
        "growth_accel_score", "quality_score",
        "gap_score_norm", "total_score", "score_rank",
    ]

    available_cols = [c for c in csv_cols if c in df.columns]
    df[available_cols].to_csv(path, index=False, encoding="utf-8-sig")
    eprint(f"     CSV saved: {path} ({len(df)} rows)")
    return path


def save_report(report_text, path=None):
    """Save markdown report."""
    if path is None:
        path = OUT_REPORT
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text)
    eprint(f"     Report saved: {path} ({len(report_text)} chars)")
    return path


# ══════════════════════════════════════════════════════════════════════
# Main Orchestrator
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="半年报投资决策系统 - Semi-Annual Investment Decision System"
    )
    parser.add_argument("--out", type=str, default=str(OUT_CSV),
                        help=f"Output CSV path (default: {OUT_CSV})")
    parser.add_argument("--report", type=str, default=str(OUT_REPORT),
                        help=f"Output report path (default: {OUT_REPORT})")
    parser.add_argument("--no-report", action="store_true",
                        help="Skip report generation")
    parser.add_argument("--codes", type=str, default=None,
                        help="Comma-separated stock codes to limit scope")
    parser.add_argument("--no-price", action="store_true",
                        help="Skip price data fetch (faster, for preview)")
    parser.add_argument("--no-eastmoney", action="store_true",
                        help="Skip Eastmoney enrichment (faster)")
    args = parser.parse_args()

    out_csv = Path(args.out)
    out_report = Path(args.report)

    eprint("=" * 60)
    eprint(f"  半年报投资决策系统  {TODAY.strftime('%Y-%m-%d %H:%M')}")
    eprint(f"  最后交易日: {LAST_TRADE_DAY.strftime('%Y-%m-%d')}")
    eprint("=" * 60)

    # Step 1: Fetch forecasts
    fc_df = fetch_forecasts()

    # Step 2: Fetch express reports
    ex_df = fetch_express_reports()

    # Step 3: Merge and deduplicate
    eprint("     合并预告和快报...")
    if not fc_df.empty and not ex_df.empty:
        # Express takes priority for actual data; keep forecast info
        all_codes = set(fc_df["code"].tolist() + ex_df["code"].tolist())
        merged_rows = []
        for code in all_codes:
            ex_row = ex_df[ex_df["code"] == code]
            fc_row = fc_df[fc_df["code"] == code]
            if not ex_row.empty:
                row = ex_row.iloc[0].to_dict()
                if not fc_row.empty:
                    fc = fc_row.iloc[0]
                    row["forecast_type"] = fc.get("forecast_type", "")
                    row["forecast_lower_yi"] = fc.get("forecast_lower_yi")
                    row["forecast_upper_yi"] = fc.get("forecast_upper_yi")
                    row["forecast_mid_yi"] = fc.get("forecast_mid_yi")
                    row["h1_forecast_yoy"] = fc.get("h1_forecast_yoy")
                    row["h1_prior_profit_yi"] = fc.get("h1_prior_profit_yi")
                    row["change_text"] = fc.get("change_text", "")
                else:
                    for k in ["forecast_type", "forecast_lower_yi", "forecast_upper_yi",
                              "forecast_mid_yi", "h1_forecast_yoy", "h1_prior_profit_yi", "change_text"]:
                        row.setdefault(k, np.nan if "yi" in k or "yoy" in k else "")
                merged_rows.append(row)
            elif not fc_row.empty:
                merged_rows.append(fc_row.iloc[0].to_dict())
        df = pd.DataFrame(merged_rows)
    elif not fc_df.empty:
        df = fc_df
    elif not ex_df.empty:
        df = ex_df
    else:
        eprint("  ERROR: No data from any source!")
        sys.exit(1)

    eprint(f"  Merged: {len(df)} unique stocks (预告: {len(fc_df)}, 快报: {len(ex_df)})")

    # Limit to specific codes if requested
    if args.codes:
        target_codes = set(c.strip() for c in args.codes.split(","))
        df = df[df["code"].isin(target_codes)]
        eprint(f"  Filtered to {len(df)} stocks (--codes)")

    # Step 4: Fetch income statements
    all_codes = df["code"].unique().tolist()
    fin_data = fetch_income_statements(all_codes)

    # Step 5: Calculate Q2
    df = calculate_q2(df, fin_data)

    # Step 6: Compute gap factor
    df = compute_gap_factor(df)

    # Step 7: Enrich with Eastmoney data
    if not args.no_eastmoney:
        df = enrich_eastmoney(df)
    else:
        for col in ["mkt_cap_yi", "industry", "concepts", "mkt_cap_tier"]:
            if col not in df.columns:
                df[col] = np.nan
        df["mkt_cap_tier"] = df["mkt_cap_tier"].fillna("N/A")

    # Step 8: Fetch price data and compute event metrics
    if not args.no_price and len(df) > 0:
        price_cache = fetch_price_data(all_codes, start_date="20260401", end_date=TODAY_STR)
        df = compute_all_event_metrics(df, price_cache)
    else:
        for col in [
            "open_gap_pct", "intraday_pct", "day_return_pct", "vol_ratio",
            "car_3d", "car_5d", "car_10d",
            "car_3d_abnormal", "car_5d_abnormal",
            "gap_filled", "event_days_available",
        ]:
            df[col] = np.nan

    # Step 9: Composite scoring
    df = compute_composite_score(df)

    # Step 10: Save CSV
    save_csv(df, out_csv)

    # Step 11: Generate and save report
    if not args.no_report:
        report = generate_report(df)
        save_report(report, out_report)
        # Print summary to stdout
        print(report[:500])
        if len(report) > 500:
            print(f"... (full report: {out_report})")
    else:
        # Print brief summary
        top5 = df.nlargest(5, "total_score")
        print(f"Top 5 by total_score:")
        for _, row in top5.iterrows():
            print(f"  {row['code']} {row['name']}: {row['total_score']:.2f} "
                  f"(gap={row.get('gap_level','N/A')}, "
                  f"open_gap={row.get('open_gap_pct','N/A')})")

    eprint("=" * 60)
    eprint("  Done.")
    eprint(f"  CSV: {out_csv}")
    if not args.no_report:
        eprint(f"  Report: {out_report}")
    eprint("=" * 60)


if __name__ == "__main__":
    main()

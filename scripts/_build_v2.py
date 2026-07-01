#!/usr/bin/env python3
"""Build the v2 semiannual investment system script."""
import os

script = []
script.append('''#!/usr/bin/env python3
"""
Semi-Annual Investment Decision System v2.0
4-Factor Z-Score Model + Historical Backtesting
DeerFlow Audit Refactor:
  - 7 factors -> 4 factors (SUE 35%, Price Confirm 30%, Pre-CAR Inverse 20%, Industry Resonance 15%)
  - Percentile scoring -> Z-Score standardization
  - New: Pre-announcement CAR filter (penalize priced-in)
  - New: Liquidity/quality filter layer
  - New: Historical backtesting (2025 H1 data, factor IC validation)
  - Removed: raw Q2 growth, growth accel, standalone ROE, analyst revision
"""
import cjpy
import akshare as ak
import pandas as pd
import numpy as np
import json, os, sys, re, time as _time, argparse, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
try:
    from scipy import stats as scipy_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

PERIOD_H1 = "20260630"
PERIOD_Q1 = "20260331"
PERIOD_H1_PRIOR = "20250630"
PERIOD_Q1_PRIOR = "20250331"
BT_PERIOD_H1 = "20250630"
BT_PERIOD_Q1 = "20250331"
BT_PERIOD_H1_PRIOR = "20240630"
BT_PERIOD_Q1_PRIOR = "20240331"

TODAY = datetime.now()
TODAY_STR = TODAY.strftime("%Y%m%d")
OUT_CSV = Path.home() / "fmdata/store/fundamentals/semiannual_investment.csv"
OUT_REPORT = Path.home() / "fmdata/store/fundamentals/semiannual_investment_report.md"
BT_OUT = Path.home() / "fmdata/store/fundamentals/semiannual_backtest.json"
CJPY_BATCH = 500
EASTMONEY_DELAY = 0.15

WEIGHTS = {"sue": 0.35, "price_confirm": 0.30, "pre_car_inverse": 0.20, "industry_resonance": 0.15}
FILTER_MIN_MKT_CAP_YI = 50
FILTER_MIN_TURNOVER_YI = 0.5
FILTER_MIN_ROE = 0
SUE_HISTORICAL_QUARTERS = 8
''')

# ── Helpers ──
script.append('''
def eprint(*a, **k):
    print(*a, file=sys.stderr); sys.stderr.flush()

def last_trading_day(ref_date=None):
    d = (ref_date or TODAY) - timedelta(days=0)
    while d.weekday() >= 5: d -= timedelta(days=1)
    try:
        r = requests.get(f"http://127.0.0.1:1934/calendar/is-trade-day?date={d.strftime('%Y%m%d')}", timeout=3)
        if r.status_code == 200 and not r.json().get("is_trade_day", True):
            while True:
                d -= timedelta(days=1)
                while d.weekday() >= 5: d -= timedelta(days=1)
                r = requests.get(f"http://127.0.0.1:1934/calendar/is-trade-day?date={d.strftime('%Y%m%d')}", timeout=3)
                if r.status_code != 200 or r.json().get("is_trade_day", True): break
    except Exception: pass
    return d

LAST_TRADE_DAY = last_trading_day()

def to_cjpy_code(code):
    code = str(code).zfill(6)
    if code[:3] in ("600","601","603","605","688","689"): return f"SH{code}"
    if code[:3] in ("000","001","002","003","004","300","301"): return f"SZ{code}"
    if code[0] in ("4","8","9"): return f"BJ{code}"
    return None

def to_raw_code(c): return str(c)[2:] if c and len(str(c)) >= 8 else str(c).zfill(6)

def _gr(c, p):
    try:
        c,p = float(c), float(p)
        return (c/p-1)*100 if p != 0 and not (np.isnan(c) or np.isnan(p)) else np.nan
    except: return np.nan

def parse_forecast_range(text):
    if pd.isna(text) or not isinstance(text, str): return None,None,None
    text = text.replace(",","").replace("\\uff0c","")
    def _pa(s):
        s = s.strip()
        try:
            if "\\u4ebf" in s: return float(s.replace("\\u4ebf\\u5143",""))
            if "\\u4e07" in s: return float(s.replace("\\u4e07\\u5143",""))/10000
            if "\\u5143" in s: return float(s.replace("\\u5143",""))/1e8
            return float(s)/1e8
        except: return None
    m = re.search(r'(?:\\u76c8\\u5229|\\u51c0\\u5229\\u6da6)[\\uff1a:]?\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)\\s*[\\u81f3\\uff5e~-]\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)', text)
    if m:
        lo,hi = _pa(m.group(1)), _pa(m.group(2))
        if lo is not None and hi is not None: return lo,hi,(lo+hi)/2
    m = re.search(r'(?:\\u76c8\\u5229|\\u51c0\\u5229\\u6da6)[\\u7ea6]?[\\uff1a:]?\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)', text)
    if m:
        v = _pa(m.group(1))
        if v is not None: return v,v,v
    m = re.search(r'\\u4e8f\\u635f[\\uff1a:]?\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)\\s*[\\u81f3\\uff5e~-]\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)', text)
    if m:
        lo,hi = _pa(m.group(1)), _pa(m.group(2))
        if lo is not None and hi is not None: return -hi,-lo,-(lo+hi)/2
    m = re.search(r'\\u4e8f\\u635f[\\uff1a:]?\\s*([\\d.]+(?:\\u4ebf|\\u4e07)?\\u5143?)', text)
    if m:
        v = _pa(m.group(1))
        if v is not None: return -v,-v,-v
    return None,None,None

def get_mkt_cap_tier(mkt_cap_yi):
    if mkt_cap_yi is None or pd.isna(mkt_cap_yi) or mkt_cap_yi <= 0: return "N/A"
    if mkt_cap_yi >= 1000: return "\\u5927\\u76d8"
    if mkt_cap_yi >= 500: return "\\u4e2d\\u5927\\u76d8"
    if mkt_cap_yi >= 100: return "\\u4e2d\\u76d8"
    if mkt_cap_yi >= 50: return "\\u4e2d\\u5c0f\\u76d8"
    return "\\u5c0f\\u76d8"

def secid_from_code(code):
    code = str(code).zfill(6)
    return f"1.{code}" if code[0] == "6" else f"0.{code}"
''')

# ── Phase 1: Data Collection ──
script.append('''

# ============================================================
# Phase 1: Data Collection
# ============================================================

def fetch_forecasts(period="20260630"):
    eprint("[1/4] Fetching forecasts...")
    try:
        df = ak.stock_yjyg_em(date=period)
        if df is None or df.empty: return pd.DataFrame()
    except Exception as e:
        eprint(f"  ERROR: {e}"); return pd.DataFrame()
    eprint(f"  Raw: {len(df)} rows")
    mask = df["\\u9884\\u6d4b\\u6307\\u6807"] == "\\u5f52\\u5c5e\\u4e8e\\u4e0a\\u5e02\\u516c\\u53f8\\u80a1\\u4e1c\\u7684\\u51c0\\u5229\\u6da6"
    df = df[mask].copy()
    eprint(f"  After filter to net profit: {len(df)}")
    if df.empty: return df
    df["code"] = df["\\u80a1\\u7968\\u4ee3\\u7801"].astype(str).str.zfill(6)
    df["name"] = df["\\u80a1\\u7968\\u7b80\\u79f0"].astype(str)
    df["notice_date"] = pd.to_datetime(df["\\u516c\\u544a\\u65e5\\u671f"], errors="coerce")
    df["forecast_type"] = df["\\u9884\\u544a\\u7c7b\\u578b"].astype(str)
    df["h1_forecast_yoy"] = pd.to_numeric(df["\\u4e1a\\u7ee9\\u53d8\\u52a8\\u5e45\\u5ea6"], errors="coerce")
    df["h1_prior_profit_yi"] = pd.to_numeric(df["\\u4e0a\\u5e74\\u540c\\u671f\\u503c"], errors="coerce") / 1e8
    df["change_text"] = df["\\u4e1a\\u7ee9\\u53d8\\u52a8"].astype(str)
    ranges = df["change_text"].apply(parse_forecast_range)
    df["forecast_lower_yi"] = ranges.apply(lambda x: x[0])
    df["forecast_upper_yi"] = ranges.apply(lambda x: x[1])
    df["forecast_mid_yi"] = ranges.apply(lambda x: x[2])
    raw_val = pd.to_numeric(df["\\u9884\\u6d4b\\u6570\\u503c"], errors="coerce")/1e8
    mask_no = df["forecast_mid_yi"].isna()
    df.loc[mask_no, "forecast_mid_yi"] = raw_val[mask_no]
    df.loc[mask_no, "forecast_lower_yi"] = raw_val[mask_no]*0.9
    df.loc[mask_no, "forecast_upper_yi"] = raw_val[mask_no]*1.1
    df["source"] = "\\u9884\\u544a"
    return df[["code","name","source","notice_date","forecast_type","forecast_lower_yi","forecast_upper_yi","forecast_mid_yi","h1_forecast_yoy","h1_prior_profit_yi","change_text"]].reset_index(drop=True)

def fetch_express_reports(period="20260630"):
    eprint("[2/4] Fetching express reports...")
    try:
        df = ak.stock_yjkb_em(date=period)
        if df is None or df.empty:
            eprint("  No express reports yet (expected early in season)")
            return pd.DataFrame()
    except Exception as e:
        eprint(f"  Not available: {e}"); return pd.DataFrame()
    eprint(f"  Got {len(df)} rows")
    df["code"] = df["\\u80a1\\u7968\\u4ee3\\u7801"].astype(str).str.zfill(6)
    df["name"] = df["\\u80a1\\u7968\\u7b80\\u79f0"].astype(str)
    df["notice_date"] = pd.to_datetime(df["\\u516c\\u544a\\u65e5\\u671f"], errors="coerce")
    rev_col = next((c for c in df.columns if "\\u8425\\u4e1a" in c and "\\u6536\\u5165" in c), None)
    prof_col = next((c for c in df.columns if "\\u51c0\\u5229\\u6da6" in c), None)
    df["h1_revenue_yi"] = pd.to_numeric(df[rev_col], errors="coerce")/1e8 if rev_col else np.nan
    df["h1_profit_yi"] = pd.to_numeric(df[prof_col], errors="coerce")/1e8 if prof_col else np.nan
    df["source"] = "\\u5feb\\u62a5"
    cols = ["code","name","source","notice_date","h1_revenue_yi","h1_profit_yi"]
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)
''')

print("Building script...")
script_text = "\\n".join(script)
with open("/home/ubuntu/fmdata/scripts/_build_v2_output.py", "w") as f:
    f.write(script_text)
print(f"Written {len(script_text)} chars")

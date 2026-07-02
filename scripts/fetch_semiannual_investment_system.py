#!/usr/bin/env python3
"""
Semi-Annual Investment Decision System v2.0
4-Factor Z-Score Model + Historical Backtesting

DeerFlow Audit Refactor:
  4 factors: SUE(35%%) + Price Confirm(30%%) + Pre-CAR Inverse(20%%) + Industry Resonance(15%%)
  Z-Score standardization | Pre-CAR filter | Liquidity/quality filter | Backtest (2025 H1)
  Removed: raw Q2 growth, growth accel, standalone ROE, analyst revision
"""
import cjpy, akshare as ak, pandas as pd, numpy as np
import json, os, sys, re, time as _time, argparse, requests
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

try:
    from scipy import stats as scipy_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ---- Period Config ----
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

# ---- Static Reference Data (fallback when Eastmoney proxy pool fails) ----
# Industry + market cap estimates for our tracked stocks.
# Updated via: fmdata stock-daily close × known total_share = estimated mkt_cap.
STATIC_REFERENCE = {
    '000601': {'industry': '水力发电', 'ref_mkt_cap_yi': 63.5},
    '000603': {'industry': '有色金属', 'ref_mkt_cap_yi': 132.6},
    '000703': {'industry': '炼油化工', 'ref_mkt_cap_yi': 484.4},
    '001237': {'industry': '医疗器械', 'ref_mkt_cap_yi': 96.1},
    '001248': {'industry': '新能源发电', 'ref_mkt_cap_yi': 300.0},   # IPO, estimated
    '001365': {'industry': '电子化学品', 'ref_mkt_cap_yi': 100.2},
    '001393': {'industry': '电子化学品', 'ref_mkt_cap_yi': 80.5},
    '001399': {'industry': '电子化学品', 'ref_mkt_cap_yi': 102.3},
    '002048': {'industry': '汽车零部件', 'ref_mkt_cap_yi': 186.1},
    '002326': {'industry': '化学原料药', 'ref_mkt_cap_yi': 215.5},
    '002458': {'industry': '畜禽养殖', 'ref_mkt_cap_yi': 87.8},
    '002475': {'industry': '消费电子', 'ref_mkt_cap_yi': 5043.5},
    '002568': {'industry': '食品加工', 'ref_mkt_cap_yi': 178.0},
    '002648': {'industry': '化学制品', 'ref_mkt_cap_yi': 1257.4},
    '002915': {'industry': '氟化工', 'ref_mkt_cap_yi': 67.1},
    '300014': {'industry': '电池', 'ref_mkt_cap_yi': 1330.5},
    '300221': {'industry': '改性塑料', 'ref_mkt_cap_yi': 80.0},
    '300497': {'industry': '化学原料药', 'ref_mkt_cap_yi': 243.8},
    '300604': {'industry': '半导体设备', 'ref_mkt_cap_yi': 1881.2},
    '300671': {'industry': '模拟芯片', 'ref_mkt_cap_yi': 796.1},
    '300867': {'industry': '固废治理', 'ref_mkt_cap_yi': 49.3},
    '301531': {'industry': '自动化设备', 'ref_mkt_cap_yi': 124.5},
    '600233': {'industry': '快递', 'ref_mkt_cap_yi': 495.3},
    '600256': {'industry': '能源及重型设备', 'ref_mkt_cap_yi': 319.5},
    '600872': {'industry': '调味品', 'ref_mkt_cap_yi': 132.6},
    '601005': {'industry': '普钢', 'ref_mkt_cap_yi': 104.2},
    '688635': {'industry': '半导体', 'ref_mkt_cap_yi': 827.2},
    '688797': {'industry': '医疗美容', 'ref_mkt_cap_yi': 1297.4},
}

# ---- 4-Factor Weights (DeerFlow v2.0) ----
WEIGHTS = {"sue": 0.35, "price_confirm": 0.30, "pre_car_inverse": 0.20, "industry_resonance": 0.15}
# ---- Filter Thresholds ----
FILTER_MIN_MKT_CAP_YI = 50
FILTER_MIN_TURNOVER_YI = 0.5
FILTER_MIN_ROE = 0
# ---- SUE Config ----
SUE_HISTORICAL_QUARTERS = 12  # 3 years for seasonal ratio + std estimation
SEASONAL_MIN_YEARS = 2        # minimum years of Q2/Q1 data for seasonal proxy

# Forecast type boost for SUE signal strength
FORECAST_TYPE_BOOST = {
    "扭亏": 1.5, "预增": 1.0, "大增": 1.0, "略增": 0.3,
    "续盈": 0.2, "不确定": 0.0,
    "预减": -0.5, "略减": -0.5, "首亏": -1.0, "续亏": -1.0,
}
def eprint(*a, **k): print(*a, file=sys.stderr); sys.stderr.flush()

def last_trading_day(ref_date=None):
    d = (ref_date or TODAY) - timedelta(days=0)
    while d.weekday() >= 5: d -= timedelta(days=1)
    try:
        r = requests.get(f"http://127.0.0.1:1934/reference/calendar?date={d.strftime('%Y%m%d')}", timeout=3)
        if r.status_code == 200 and not r.json().get("is_trade_day", True):
            while True:
                d -= timedelta(days=1)
                while d.weekday() >= 5: d -= timedelta(days=1)
                r = requests.get(f"http://127.0.0.1:1934/reference/calendar?date={d.strftime('%Y%m%d')}", timeout=3)
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

def is_bj_stock(code):
    """BJ stocks (920xxx/83xxxx/87xxxx) — exclude from analysis."""
    return str(code).zfill(6)[0] in ("4", "8", "9")

def _gr(c, p):
    try:
        c,p = float(c), float(p)
        return (c/p-1)*100 if p != 0 and not (np.isnan(c) or np.isnan(p)) else np.nan
    except: return np.nan

def parse_forecast_range(text):
    """Parse profit range in 亿 from forecast change text."""
    if pd.isna(text) or not isinstance(text, str): return None,None,None
    text = text.replace(",","").replace("，","")
    def _pa(s):
        s = s.strip()
        try:
            if "亿" in s: return float(s.replace("亿元",""))
            if "万" in s: return float(s.replace("万元",""))/10000
            if "元" in s: return float(s.replace("元",""))/1e8
            return float(s)/1e8
        except: return None
    m = re.search(r"(?:盈利|净利润)[：:]?\s*([\d.]+(?:亿|万)?元?)\s*[至～~-]\s*([\d.]+(?:亿|万)?元?)", text)
    if m:
        lo,hi = _pa(m.group(1)), _pa(m.group(2))
        if lo is not None and hi is not None: return lo,hi,(lo+hi)/2
    m = re.search(r"(?:盈利|净利润)[约]?[：:]?\s*([\d.]+(?:亿|万)?元?)", text)
    if m:
        v = _pa(m.group(1))
        if v is not None: return v,v,v
    m = re.search(r"亏损[：:]?\s*([\d.]+(?:亿|万)?元?)\s*[至～~-]\s*([\d.]+(?:亿|万)?元?)", text)
    if m:
        lo,hi = _pa(m.group(1)), _pa(m.group(2))
        if lo is not None and hi is not None: return -hi,-lo,-(lo+hi)/2
    m = re.search(r"亏损[：:]?\s*([\d.]+(?:亿|万)?元?)", text)
    if m:
        v = _pa(m.group(1))
        if v is not None: return -v,-v,-v
    return None,None,None

def get_mkt_cap_tier(mkt_cap_yi):
    if mkt_cap_yi is None or pd.isna(mkt_cap_yi) or mkt_cap_yi <= 0: return "N/A"
    if mkt_cap_yi >= 1000: return "大盘(>=千亿)"
    if mkt_cap_yi >= 500: return "中大盘(500-1000亿)"
    if mkt_cap_yi >= 100: return "中盘(100-500亿)"
    if mkt_cap_yi >= 50: return "中小盘(50-100亿)"
    return "小盘(<50亿)"

def secid_from_code(code):
    code = str(code).zfill(6)
    if code[0] == "6": return f"1.{code}"       # SH
    if code[0] in ("0","3"): return f"0.{code}" # SZ
    if code[0] in ("4","8","9"): return f"0.{code}" # BJ - eastmoney uses 0. prefix
    return f"0.{code}"

# ---- fmdata fallback for Q1 data (stocks without cjpy coverage) ----

def fetch_q1_from_fmdata(codes):
    """Fetch Q1 2026 net profit from fmdata actual_financials as cjpy fallback.
    Returns dict: raw_code -> {"q1_profit": float or None, "q1_prior_profit": float or None}
    """
    import urllib.parse
    result = {}
    if not codes: return result
    try:
        codes_str = ",".join(str(c).zfill(6) for c in codes)
        url = f"http://127.0.0.1:1934/data/actual_financials?codes={urllib.parse.quote(codes_str)}"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", []) if isinstance(data, dict) else data
        for row in rows:
            secucode = str(row.get("SECUCODE", ""))
            code = secucode.split(".")[0] if "." in secucode else secucode
            if not code or code.isdigit():
                code = str(code).zfill(6) if code.isdigit() else code
            else:
                continue
            report_date = row.get("REPORTDATE")
            qdate = str(row.get("QDATE", ""))
            basic_eps = row.get("BASIC_EPS")
            bps = row.get("BPS")

            if code not in result:
                result[code] = {}

            # Q1 = REPORTDATE=20260331, report is Q1 2026
            if report_date == 20260331:
                # basic_eps is cumulative for Q1 report. We need net profit.
                # net_profit = basic_eps * shares. If not available, mark as partial.
                result[code]["q1_eps"] = basic_eps
                result[code]["q1_bps"] = bps
            # Q1 prior = REPORTDATE=20250331
            elif report_date == 20250331:
                result[code]["q1_prior_eps"] = basic_eps
    except Exception as e:
        eprint(f"  fmdata Q1 fallback: {e}")
    return result

# ---- Z-Score Helpers ----

def rank_pct(series, ascending=True, neutral_default=50.0):
    """Convert factor values to percentile ranks (0-100), robust for N<30.
    N=2: returns default for all; N>=3: rank-based percentile.
    Missing values get neutral_default."""
    result = pd.Series(float(neutral_default), index=series.index)
    valid = series.dropna()
    if len(valid) < 2:
        return result
    if not ascending:
        valid = -valid
    ranks = valid.rank(pct=True) * 100.0
    result[valid.index] = ranks
    return result

def rank_pct_neutral(series, ascending=True):
    """Percentile ranks with missing→50 (neutral midpoint)."""
    return rank_pct(series, ascending=ascending, neutral_default=50.0)

def spearman_ic(scores, returns):
    """Spearman rank IC between scores and forward returns."""
    mask = scores.notna() & returns.notna()
    if mask.sum() < 5: return np.nan
    if HAS_SCIPY:
        ic, _ = scipy_stats.spearmanr(scores[mask], returns[mask])
        return ic
    else:
        return scores[mask].corr(returns[mask], method="spearman")

# ============================================================
# Phase 1: Data Collection
# ============================================================

def fetch_forecasts_from_tushare(period="20260630"):
    """Fetch H1 performance forecasts from tushare via fmdata (no proxy needed).
    Returns unified DataFrame with same columns as akshare path, or empty DataFrame."""
    import requests as _req
    eprint("[1a/5] Fetching forecasts (tushare, no proxy)...")
    try:
        resp = _req.get("http://127.0.0.1:1934/data/performance_forecast", timeout=30)
        raw = resp.json()
        items = raw.get("data", raw if isinstance(raw, list) else [])
        if not isinstance(items, list):
            eprint("  tushare forecast: unexpected response"); return pd.DataFrame(), "error"
    except Exception as e:
        eprint(f"  tushare forecast: fetch failed ({e})"); return pd.DataFrame(), "error"

    # Filter to period
    try: rp = int(period)
    except: rp = 20260630
    rows = []
    for it in items:
        rd = it.get("REPORT_DATE") or it.get("report_date") or 0
        if rd != rp: continue
        # Only net profit forecasts
        pred_type = str(it.get("PREDICT_TYPE") or it.get("FORECAST_TYPE") or "")
        sec_code = str(it.get("SECURITY_CODE") or it.get("TS_CODE") or "")
        if not sec_code: continue
        code = sec_code.zfill(6)
        name = str(it.get("SECURITY_NAME_ABBR") or it.get("SECURITY_SHORT_NAME") or "")
        ndate = str(it.get("NOTICE_DATE") or it.get("ANN_DATE") or "")
        # tushare 值单位: 元
        lower = float(it.get("PREDICT_AMT_LOWER") or it.get("PREDICT_AMT_LOWER_YI") or 0)
        upper = float(it.get("PREDICT_AMT_UPPER") or it.get("PREDICT_AMT_UPPER_YI") or 0)
        prev = float(it.get("PREYEAR_SAME_PERIOD") or it.get("PREYEAR_SAME_PERIOD_YI") or 0)
        # Convert to 亿 if in 元
        if lower > 1000 or upper > 1000: lower, upper, prev = lower/1e8, upper/1e8, prev/1e8
        add_lower = float(it.get("ADD_AMP_LOWER") or 0)
        add_upper = float(it.get("ADD_AMP_UPPER") or 0)
        mid = (lower + upper) / 2 if (lower > 0 or upper > 0) else np.nan
        yoy = (add_lower + add_upper) / 2
        rows.append({"code": code, "name": name, "source": "tushare预告", "notice_date": ndate,
                     "forecast_type": pred_type, "forecast_lower_yi": lower, "forecast_upper_yi": upper,
                     "forecast_mid_yi": mid, "h1_forecast_yoy": yoy, "h1_prior_profit_yi": prev,
                     "change_text": f"{pred_type} {lower:.2f}-{upper:.2f}亿"})

    if not rows:
        eprint(f"  tushare forecast: 0 records for {period}")
        return pd.DataFrame(), "empty"

    df = pd.DataFrame(rows)
    # Dedup: keep the record with largest forecast_mid_yi per stock code
    dup_mask = df.duplicated(subset=["code"], keep=False)
    if dup_mask.any():
        dup_codes = df.loc[dup_mask, "code"].unique()
        for dc in dup_codes:
            dup_rows = df[df["code"] == dc]
            best_idx = dup_rows["forecast_mid_yi"].fillna(0).argmax()
            best_max = dup_rows["forecast_mid_yi"].max()
            if pd.isna(best_max): best_max = 0
            eprint(f"  dedup {dc} {dup_rows.iloc[0]['name']}: {len(dup_rows)} rows -> keep "
                   f"'{dup_rows.iloc[best_idx]['forecast_type']}' "
                   f"({best_max:.2f}亿)")
        df = df.sort_values("forecast_mid_yi", ascending=False, na_position="last")
        df = df.drop_duplicates(subset=["code"], keep="first")
        eprint(f"  tushare forecast dedup: {len(rows)} rows -> {len(df)} stocks")
    # Parse dates
    df["notice_date"] = pd.to_datetime(df["notice_date"], errors="coerce")
    cols = ["code","name","source","notice_date","forecast_type",
            "forecast_lower_yi","forecast_upper_yi","forecast_mid_yi",
            "h1_forecast_yoy","h1_prior_profit_yi","change_text"]
    eprint(f"  tushare forecast: {len(df)} stocks (H1 {period})")
    return df[cols].reset_index(drop=True), "ok"


def fetch_forecasts(period="20260630"):
    """Fetch H1 performance forecasts: tushare first (no proxy), akshare fallback."""
    eprint("[1/5] Fetching forecasts...")

    # Try tushare first (no proxy, more reliable)
    df_ts, ts_status = fetch_forecasts_from_tushare(period)
    if ts_status == "ok" and len(df_ts) > 0:
        eprint(f"  ✅ {len(df_ts)} stocks from tushare (no proxy needed)")
        return df_ts

    # Fallback to akshare
    eprint("  🔶 tushare empty/error, falling back to akshare (needs proxy)...")
    try:
        df = ak.stock_yjyg_em(date=period)
        if df is None or df.empty:
            eprint("  WARNING: no forecast data from akshare either"); return pd.DataFrame()
    except Exception as e:
        eprint(f"  ERROR: akshare also failed ({e})"); return pd.DataFrame()

    eprint(f"  akshare raw: {len(df)} rows")
    mask = df["预测指标"] == "归属于上市公司股东的净利润"
    df = df[mask].copy()
    eprint(f"  After filter to net profit: {len(df)}")
    if df.empty: return df

    df["code"] = df["股票代码"].astype(str).str.zfill(6)
    df["name"] = df["股票简称"].astype(str)
    df["notice_date"] = pd.to_datetime(df["公告日期"], errors="coerce")
    df["forecast_type"] = df["预告类型"].astype(str)
    df["h1_forecast_yoy"] = pd.to_numeric(df["业绩变动幅度"], errors="coerce")
    df["h1_prior_profit_yi"] = pd.to_numeric(df["上年同期值"], errors="coerce") / 1e8
    df["change_text"] = df["业绩变动"].astype(str)

    ranges = df["change_text"].apply(parse_forecast_range)
    df["forecast_lower_yi"] = ranges.apply(lambda x: x[0])
    df["forecast_upper_yi"] = ranges.apply(lambda x: x[1])
    df["forecast_mid_yi"] = ranges.apply(lambda x: x[2])

    raw_val = pd.to_numeric(df["预测数值"], errors="coerce") / 1e8
    mask_no = df["forecast_mid_yi"].isna()
    df.loc[mask_no, "forecast_mid_yi"] = raw_val[mask_no]
    df.loc[mask_no, "forecast_lower_yi"] = raw_val[mask_no] * 0.9
    df.loc[mask_no, "forecast_upper_yi"] = raw_val[mask_no] * 1.1

    df["source"] = "akshare预告"
    cols = ["code","name","source","notice_date","forecast_type",
            "forecast_lower_yi","forecast_upper_yi","forecast_mid_yi",
            "h1_forecast_yoy","h1_prior_profit_yi","change_text"]
    eprint(f"  akshare forecast: {len(df)} stocks")
    return df[cols].reset_index(drop=True)

def fetch_express_reports(period="20260630"):
    """Fetch H1 express reports from akshare."""
    eprint("[2/5] Fetching express reports...")
    try:
        df = ak.stock_yjkb_em(date=period)
        if df is None or df.empty:
            eprint("  No express reports yet (expected early in season)")
            return pd.DataFrame()
    except Exception as e:
        eprint(f"  Not available: {e}"); return pd.DataFrame()

    eprint(f"  Got {len(df)} rows")
    df["code"] = df["股票代码"].astype(str).str.zfill(6)
    df["name"] = df["股票简称"].astype(str)
    df["notice_date"] = pd.to_datetime(df["公告日期"], errors="coerce")

    rev_col = next((c for c in df.columns if "营业" in c and "收入" in c), None)
    prof_col = next((c for c in df.columns if "净利润" in c), None)
    df["h1_revenue_yi"] = pd.to_numeric(df[rev_col], errors="coerce") / 1e8 if rev_col else np.nan
    df["h1_profit_yi"] = pd.to_numeric(df[prof_col], errors="coerce") / 1e8 if prof_col else np.nan
    df["source"] = "快报"
    cols = ["code","name","source","notice_date","h1_revenue_yi","h1_profit_yi"]
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)

def fetch_income_statements(codes, h1_period, q1_period, h1_prior, q1_prior):
    """Fetch consolidated income statements via cjpy. Returns dict: code->{h1_c,q1_c,h1_p,q1_p}."""
    eprint("[3/5] Fetching income statements (cjpy)...")
    if not codes: return {}

    cjpy_codes, code_map = [], {}
    for c in codes:
        cj = to_cjpy_code(c)
        if cj:
            cjpy_codes.append(cj)
            code_map[cj] = c

    if not cjpy_codes:
        eprint("  No valid cjpy codes"); return {}

    fin_data, session = {}, requests.Session()
    for i in range(0, len(cjpy_codes), CJPY_BATCH):
        batch = cjpy_codes[i:i + CJPY_BATCH]
        eprint(f"  cjpy batch {i//CJPY_BATCH+1}/{(len(cjpy_codes)-1)//CJPY_BATCH+1}: {len(batch)} stocks")
        try:
            df = cjpy.get_table_data(batch, "合并利润表")
        except Exception as e:
            eprint(f"  ERROR cjpy batch: {e}"); continue

        if df is None or df.empty: continue
        df["period"] = df["截止日"].astype(str)

        for cj_code, grp in df.groupby("CODE"):
            raw_code = code_map.get(cj_code, to_raw_code(cj_code))
            periods = {row["period"]: row for _, row in grp.iterrows()}

            entry = {"h1_current": None, "q1_current": None, "h1_prior": None, "q1_prior": None}
            for key, pid in [("h1_current", h1_period), ("q1_current", q1_period),
                              ("h1_prior", h1_prior), ("q1_prior", q1_prior)]:
                if pid in periods:
                    row = periods[pid]
                    rev = row.get("营业收入", None)
                    prof = row.get("归属于母公司所有者净利润", row.get("净利润", None))
                    try:
                        rev = float(rev)/1e8 if rev is not None and not (isinstance(rev, float) and np.isnan(rev)) else None
                        prof = float(prof)/1e8 if prof is not None and not (isinstance(prof, float) and np.isnan(prof)) else None
                    except (ValueError, TypeError):
                        rev, prof = None, None
                    entry[key] = {"revenue": rev, "profit": prof}
            fin_data[raw_code] = entry

    session.close()
    eprint(f"  Income statements for {len(fin_data)} stocks")
    return fin_data

def fetch_historical_quarterly_profits(codes, h1_period):
    """Fetch 8 quarters of quarterly profits for SUE std computation.
    Returns dict: code -> list of quarterly profits in 亿 (oldest first)."""
    eprint("  Fetching historical quarterly profits for SUE std...")
    if not codes: return {}

    cjpy_codes, code_map = [], {}
    for c in codes:
        cj = to_cjpy_code(c)
        if cj:
            cjpy_codes.append(cj)
            code_map[cj] = c

    if not cjpy_codes: return {}

    # Get last 12 quarters to ensure we have enough after Q2 decomposition
    # Periods: from h1_period going back 12 reporting periods
    h1_year = int(h1_period[:4])
    target_periods = []
    for yr in range(h1_year, h1_year - 4, -1):
        for dt in [f"{yr}0630", f"{yr}0331", f"{yr-1}1231", f"{yr-1}0930"]:
            target_periods.append(dt)
    target_periods = target_periods[:SUE_HISTORICAL_QUARTERS + 4]  # extra buffer

    all_q_profits = defaultdict(list)
    for i in range(0, len(cjpy_codes), CJPY_BATCH):
        batch = cjpy_codes[i:i + CJPY_BATCH]
        try:
            df = cjpy.get_table_data(batch, "合并利润表")
        except Exception:
            continue
        if df is None or df.empty: continue

        df["period"] = df["截止日"].astype(str)
        for cj_code, grp in df.groupby("CODE"):
            raw_code = code_map.get(cj_code, to_raw_code(cj_code))
            periods = {row["period"]: row for _, row in grp.iterrows()}

            # Build cumulative profits by period
            cum_profits = {}
            for p, row in periods.items():
                prof = row.get("归属于母公司所有者净利润", row.get("净利润", None))
                try:
                    cum_profits[p] = float(prof)/1e8 if prof is not None else None
                except (ValueError, TypeError):
                    cum_profits[p] = None

            # Decompose to quarterly: Q1=Q1_cum, Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M
            quarterly = []
            for yr in sorted(set(str(p)[:4] for p in target_periods if str(p)[:4] in cum_profits or any(str(pp).startswith(str(yr)) for pp in cum_profits)), reverse=True):
                q1 = cum_profits.get(f"{yr}0331")
                h1 = cum_profits.get(f"{yr}0630")
                q3 = cum_profits.get(f"{yr}0930")
                fy = cum_profits.get(f"{yr}1231")
                q2 = h1 - q1 if h1 is not None and q1 is not None else None
                q3_s = q3 - h1 if q3 is not None and h1 is not None else None
                q4_s = fy - q3 if fy is not None and q3 is not None else None
                for q in [q4_s, q3_s, q2, q1]:  # reverse chronological, will reverse later
                    if q is not None: quarterly.append(q)

            quarterly.reverse()
            all_q_profits[raw_code] = quarterly[:SUE_HISTORICAL_QUARTERS]

    eprint(f"  Historical quarterly profits for {len(all_q_profits)} stocks")
    return dict(all_q_profits)

# ============================================================
# Phase 2: Q2 Decomposition + SUE Computation
# ============================================================

def calculate_q2_and_sue(df, fin_data, historical_q_profits):
    """Calculate Q1/Q2 decomposition, YoY/QoQ, then SUE proxy (pre-actual-reports phase)."""
    eprint("[4/5] Calculating Q2 and SUE...")

    # Profit fields
    cols_init = [
        # Actual financials (from income statements; may be None pre-report)
        "h1_revenue_yi_actual","h1_profit_yi_actual",
        "q1_revenue_yi","q1_profit_yi","q2_revenue_yi","q2_profit_yi",
        "q1_revenue_yi_prior","q1_profit_yi_prior","q2_revenue_yi_prior","q2_profit_yi_prior",
        # Implied Q2 from forecast (H1 forecast - Q1 actual cjpy)
        "q2_revenue_yi_implied","q2_profit_yi_implied",
        # YoY / QoQ (%)
        "h1_rev_forecast_yoy_pct","h1_prof_forecast_yoy_pct",
        "q1_rev_yoy_pct","q1_prof_yoy_pct",
        "q2_rev_yoy_pct","q2_prof_yoy_pct",
        "q2_rev_implied_yoy_pct","q2_prof_implied_yoy_pct",
        "q2_rev_implied_qoq_pct","q2_prof_implied_qoq_pct",
        # SUE (seasonal proxy)
        "sue_raw","sue_std","sue_method",
        "q2_expected_yi","seasonal_ratio",
    ]
    for col in cols_init:
        if col not in df.columns:
            df[col] = np.nan
    # String column (must be object dtype, not float64)
    if "sue_method" not in df.columns or df["sue_method"].dtype == "float64":
        df["sue_method"] = ""
    df["q2_expected_yi"] = np.nan  # ensure float
    df["seasonal_ratio"] = np.nan  # ensure float

    for idx, row in df.iterrows():
        code = row["code"]
        fd = fin_data.get(code, {})
        sue_method = "none"

        def _valid(v):
            return v is not None and not (isinstance(v, float) and np.isnan(v))

        # --- Extract financial data from cjpy income statements ---
        h1_actual_rev, h1_actual_prof = None, None
        if row.get("source") == "快报":
            h1_actual_rev = row.get("h1_revenue_yi")
            h1_actual_prof = row.get("h1_profit_yi")
        elif fd.get("h1_current"):
            h1_actual_rev = fd["h1_current"]["revenue"]
            h1_actual_prof = fd["h1_current"]["profit"]

        q1_curr = fd.get("q1_current") or {}
        h1_prior = fd.get("h1_prior") or {}
        q1_prior = fd.get("q1_prior") or {}

        q1_curr_rev = q1_curr.get("revenue")
        q1_curr_prof = q1_curr.get("profit")
        h1_prior_rev = h1_prior.get("revenue")
        h1_prior_prof = h1_prior.get("profit")
        q1_prior_rev = q1_prior.get("revenue")
        q1_prior_prof = q1_prior.get("profit")

        # --- Q1 actual (from cjpy) ---
        df.at[idx, "h1_revenue_yi_actual"] = h1_actual_rev
        df.at[idx, "h1_profit_yi_actual"] = h1_actual_prof
        df.at[idx, "q1_revenue_yi"] = q1_curr_rev
        df.at[idx, "q1_profit_yi"] = q1_curr_prof
        df.at[idx, "q1_revenue_yi_prior"] = q1_prior_rev
        df.at[idx, "q1_profit_yi_prior"] = q1_prior_prof

        # --- Q2 actual = H1 actual - Q1 actual (only when H1 actual exists) ---
        q2_rev = (h1_actual_rev - q1_curr_rev) if (_valid(h1_actual_rev) and q1_curr_rev is not None) else None
        q2_prof = (h1_actual_prof - q1_curr_prof) if (_valid(h1_actual_prof) and q1_curr_prof is not None) else None
        df.at[idx, "q2_revenue_yi"] = q2_rev
        df.at[idx, "q2_profit_yi"] = q2_prof

        # --- Q2 prior: H1_prior - Q1_prior ---
        # Fallback chain: cjpy h1_prior > forecast sheet h1_prior > None
        _h1_prior_prof = h1_prior_prof if _valid(h1_prior_prof) else row.get("h1_prior_profit_yi")
        _h1_prior_rev = h1_prior_rev if _valid(h1_prior_rev) else None  # forecast sheet doesn't have rev
        _q1_prior_prof = q1_prior_prof
        _q1_prior_rev = q1_prior_rev

        q2_prior_rev = (_h1_prior_rev - _q1_prior_rev) if (_valid(_h1_prior_rev) and _q1_prior_rev is not None) else None
        q2_prior_prof = (_h1_prior_prof - _q1_prior_prof) if (_valid(_h1_prior_prof) and _q1_prior_prof is not None) else None
        df.at[idx, "q2_revenue_yi_prior"] = q2_prior_rev
        df.at[idx, "q2_profit_yi_prior"] = q2_prior_prof

        # --- Q1 YoY ---
        df.at[idx, "q1_rev_yoy_pct"] = _gr(q1_curr_rev, q1_prior_rev)
        df.at[idx, "q1_prof_yoy_pct"] = _gr(q1_curr_prof, q1_prior_prof)

        # --- Q2 YoY (actual, only when Q2 actual exists) ---
        df.at[idx, "q2_rev_yoy_pct"] = _gr(q2_rev, q2_prior_rev)
        df.at[idx, "q2_prof_yoy_pct"] = _gr(q2_prof, q2_prior_prof)

        # --- H1 forecast YoY ---
        # H1 forecast (预告中值) vs last year H1 actual (cjpy > forecast sheet)
        forecast_mid = row.get("forecast_mid_yi")
        h1_prior_prof_2 = h1_prior_prof if _valid(h1_prior_prof) else row.get("h1_prior_profit_yi")
        if _valid(forecast_mid) and _valid(h1_prior_prof_2) and abs(h1_prior_prof_2) > 1e-8:
            df.at[idx, "h1_prof_forecast_yoy_pct"] = (forecast_mid - h1_prior_prof_2) / abs(h1_prior_prof_2) * 100
        forecast_rev = row.get("h1_revenue_yi")  # from express report, usually NaN
        if _valid(forecast_rev) and _valid(h1_prior_rev) and abs(h1_prior_rev) > 1e-8:
            df.at[idx, "h1_rev_forecast_yoy_pct"] = (forecast_rev - h1_prior_rev) / abs(h1_prior_rev) * 100

        # --- Q2 implied = H1 forecast - Q1 actual cjpy ---
        q2_rev_implied, q2_prof_implied = None, None
        if _valid(forecast_mid) and _valid(q1_curr_prof):
            q2_prof_implied = forecast_mid - q1_curr_prof
        if _valid(forecast_rev) and _valid(q1_curr_rev):
            q2_rev_implied = forecast_rev - q1_curr_rev
        df.at[idx, "q2_revenue_yi_implied"] = q2_rev_implied
        df.at[idx, "q2_profit_yi_implied"] = q2_prof_implied

        # --- Q2 implied YoY / QoQ ---
        df.at[idx, "q2_rev_implied_yoy_pct"] = _gr(q2_rev_implied, q2_prior_rev)
        df.at[idx, "q2_prof_implied_yoy_pct"] = _gr(q2_prof_implied, q2_prior_prof)
        df.at[idx, "q2_rev_implied_qoq_pct"] = _gr(q2_rev_implied, q1_curr_rev)
        df.at[idx, "q2_prof_implied_qoq_pct"] = _gr(q2_prof_implied, q1_curr_prof)

        # SUE Computation (Seasonal Proxy v3.0 — DeerFlow audit)
        hist_q = historical_q_profits.get(code, [])

        if _valid(q2_prof) and _valid(forecast_mid) and abs(forecast_mid) > 1e-8:
            # True SUE: REAL Q2 (from cjpy financials) vs forecast
            sue_method = "actual"
            if len(hist_q) >= 3:
                arr = np.array(hist_q)
                q_std = float(np.std(arr, ddof=1))
                if q_std > 0:
                    df.at[idx, "sue_raw"] = float(q2_prof - forecast_mid) / q_std
                    df.at[idx, "sue_std"] = q_std
                else:
                    df.at[idx, "sue_raw"] = float(q2_prof - forecast_mid) / abs(float(forecast_mid))
                    df.at[idx, "sue_std"] = 0
            else:
                upper = row.get("forecast_upper_yi")
                lower = row.get("forecast_lower_yi")
                if _valid(upper) and _valid(lower):
                    proxy_std = max(abs(upper - lower) / 4, abs(float(forecast_mid)) * 0.01)
                    df.at[idx, "sue_raw"] = float(q2_prof - forecast_mid) / proxy_std
                    df.at[idx, "sue_std"] = proxy_std
                else:
                    proxy_std = max(abs(float(forecast_mid)) * 0.05, 0.01)
                    df.at[idx, "sue_raw"] = float(q2_prof - forecast_mid) / proxy_std
                    df.at[idx, "sue_std"] = proxy_std
        else:
            # No actual Q2 → Seasonal Proxy SUE (DeerFlow v3.0)
            # Logic: use historical Q2/Q1 seasonal pattern as "market expectation",
            # then measure how much Q2_implied deviates from seasonal expectation
            # SUE = (Q2_implied - Q2_expected) / σ(historical Q2)

            # --- Step A: Extract seasonal Q2/Q1 ratios from hist_q ---
            # hist_q = [Q1_y1, Q2_y1, Q3_y1, Q4_y1, Q1_y2, Q2_y2, Q3_y2, Q4_y2, ...]
            seasonal_ratios, q2_values, q1_values = [], [], []
            for p in range(0, len(hist_q) - 1, 4):  # stride by year (4 quarters)
                if p + 1 < len(hist_q):
                    q1_p = hist_q[p]
                    q2_p = hist_q[p + 1]
                    if q1_p is not None and q2_p is not None and abs(q1_p) > 1e-8:
                        seasonal_ratios.append(q2_p / q1_p)
                        q2_values.append(q2_p)
                        q1_values.append(q1_p)

            # --- Step B: Compute seasonal expectation ---
            n_seasonal = len(seasonal_ratios)
            q2_expected, seasonal_ratio_median, sue_method = None, None, "none"

            if n_seasonal >= SEASONAL_MIN_YEARS and _valid(q1_curr_prof) and abs(q1_curr_prof) > 1e-8 and _valid(q2_prof_implied):
                seasonal_ratio_median = float(np.median(seasonal_ratios))
                # Cap extreme seasonal ratios (artifact of tiny Q1 denominator)
                seasonal_ratio_median = np.clip(seasonal_ratio_median, 0.2, 5.0)
                q2_expected = q1_curr_prof * seasonal_ratio_median
                # Compute std of historical Q2 values for denominator
                if len(q2_values) >= 3:
                    q2_std = float(np.std(q2_values, ddof=1))
                    if q2_std < abs(np.mean(q2_values)) * 0.05:
                        q2_std = abs(np.mean(q2_values)) * 0.1  # floor
                else:
                    q2_std = abs(np.mean(q2_values)) * 0.2

                surprise = q2_prof_implied - q2_expected
                if q2_std > 0:
                    df.at[idx, "sue_raw"] = float(surprise) / q2_std
                    df.at[idx, "sue_std"] = q2_std
                    sue_method = "seasonal"
                else:
                    df.at[idx, "sue_raw"] = float(surprise) / max(abs(q2_expected), 0.01)
                    df.at[idx, "sue_std"] = 0.01

                # Store seasonal diagnostics
                df.at[idx, "q2_expected_yi"] = round(q2_expected, 4)
                df.at[idx, "seasonal_ratio"] = round(seasonal_ratio_median, 4)

            # --- Step C: Fallback — implied H1 growth proxy ---
            if sue_method == "none":
                ftype = row.get("forecast_type", "")
                h1_yoy = row.get("h1_forecast_yoy")
                type_adj = FORECAST_TYPE_BOOST.get(ftype, 0) * 0.15

                if _valid(h1_prior_prof_2) and abs(h1_prior_prof_2) > 1e-8 and _valid(forecast_mid):
                    implied_yoy_pct = (forecast_mid - h1_prior_prof_2) / abs(h1_prior_prof_2) * 100
                    df.at[idx, "sue_raw"] = implied_yoy_pct / 30.0 + type_adj
                    upper = row.get("forecast_upper_yi")
                    lower = row.get("forecast_lower_yi")
                    if _valid(upper) and _valid(lower):
                        df.at[idx, "sue_std"] = max(abs(upper - lower) / max(abs(forecast_mid), 0.01), 0.05)
                    else:
                        df.at[idx, "sue_std"] = 0.15
                    sue_method = "growth_proxy"
                elif pd.notna(h1_yoy) and abs(h1_yoy) > 0:
                    df.at[idx, "sue_raw"] = h1_yoy / 30.0 + type_adj
                    df.at[idx, "sue_std"] = 0.15
                    sue_method = "growth_proxy"
                else:
                    df.at[idx, "sue_raw"] = FORECAST_TYPE_BOOST.get(ftype, 0)
                    df.at[idx, "sue_std"] = 1.0
                    sue_method = "type_only"

        df.at[idx, "sue_method"] = sue_method

    count_q2 = int(df["q2_profit_yi"].notna().sum())
    count_q2_implied = int(df["q2_profit_yi_implied"].notna().sum())
    count_q1 = int(df["q1_profit_yi"].notna().sum())
    count_sue = int(df["sue_raw"].notna().sum())
    eprint(f"  Q1 actual: {count_q1}/{len(df)} | Q2 actual: {count_q2} | Q2 implied: {count_q2_implied} | SUE: {count_sue}/{len(df)}")
    return df

# ============================================================
# Phase 3: Eastmoney Enrichment
# ============================================================

def enrich_industry_from_tushare(df):
    """Fetch industry classification from tushare stock_list via fmdata (no proxy needed).
    Industry is from tushare's 申万 (old standard), mapped through kw_match to 申万一级.
    Returns (df, health_report_dict).
    """
    import requests as _req

    health = {"source": "tushare stock_list", "total": 0, "matched": 0, "missing": 0, "status": "ok"}

    eprint("     Enriching industry from tushare stock_list (no proxy)...")
    if "industry" not in df.columns:
        df["industry"] = ""

    codes = df["code"].unique().tolist()
    health["total"] = len(codes)

    # Batch fetch from fmdata
    try:
        code_str = ",".join(codes)
        resp = _req.get(f"http://127.0.0.1:1934/data/stock_list", timeout=30)
        raw = resp.json()
        items = raw.get("data", raw if isinstance(raw, list) else [])
        if not isinstance(items, list):
            eprint("     ⚠️ stock_list: unexpected response")
            return df, health
    except Exception as e:
        eprint(f"     ⚠️ stock_list fetch failed: {e}")
        return df, health

    # Build code→industry map (tushare uses ts_code like "300014.SZ", strip suffix)
    ts_industry = {}
    for it in items:
        tc = str(it.get("ts_code", ""))
        if not tc: continue
        code = tc[:6]
        ind = it.get("industry", "")
        if ind:
            ts_industry[code] = ind

    # Apply from tushare
    for code in codes:
        ind = ts_industry.get(code, "")
        if ind:
            mask = df["code"] == code
            df.loc[mask, "industry"] = ind
            df.loc[mask, "industry_source"] = "tushare"
            health["matched"] += 1

    # STATIC_REFERENCE fallback for stocks not in tushare (new IPOs etc.)
    missing_codes = [c for c in codes if c not in ts_industry or not ts_industry.get(c)]
    if missing_codes:
        for code in missing_codes:
            ref = STATIC_REFERENCE.get(code, {})
            if ref.get("industry"):
                mask = df["code"] == code
                df.loc[mask, "industry"] = ref["industry"]
                df.loc[mask, "industry_source"] = "static_ref"
                health["matched"] += 1
        missing_codes = [c for c in missing_codes if c not in STATIC_REFERENCE or not STATIC_REFERENCE.get(c, {}).get("industry")]

    health["missing"] = health["total"] - health["matched"]
    if health["missing"] > 0:
        health["status"] = "warn" if health["missing"] > 3 else "ok"
        eprint(f"     🟡 tushare industry: {health['matched']}/{health['total']} matched (ts={health['matched']-health.get('static_fallback',0)}, ref={health.get('static_fallback',0)}), missing: {missing_codes}")
    else:
        eprint(f"     ✅ tushare industry: {health['matched']}/{health['total']} matched (no proxy needed)")

    return df, health


def enrich_market_cap_from_eastmoney(df):
    """Fetch market cap ONLY from Eastmoney push2 API via QG proxy pool.
    Stripped: industry/concepts are now from tushare (see enrich_industry_from_tushare).
    Triple fallback: push2 live → retry with fresh proxy → STATIC_REFERENCE.
    Returns (df, health_report_dict).
    """
    from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy

    health = {"source": "Eastmoney push2 (mkt-cap only)", "total": 0, "live": 0, "fallback": 0, "failed": 0, "status": "ok"}

    eprint("     Enriching market cap from Eastmoney (via proxy)...")
    for col in ["mkt_cap_yi"]:
        if col not in df.columns:
            df[col] = np.nan
    if "mkt_cap_tier" not in df.columns:
        df["mkt_cap_tier"] = ""

    def _try_fetch(secid, max_retries=3):
        for attempt in range(max_retries):
            if attempt > 0:
                try:
                    proxy_url = _get_qg_proxy()
                    _set_requests_proxy(proxy_url)
                except Exception:
                    pass
            try:
                # Only f116 (total_mv), stripped f127 (industry), f129 (concepts)
                url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f12,f116"
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("data"):
                    d = data["data"]
                    if d.get("f116") and d["f116"] > 0:
                        return d
                return None
            except Exception:
                continue
        return None

    def _apply_static_fallback(code):
        ref = STATIC_REFERENCE.get(code, {})
        if not ref:
            return False
        mask = df["code"] == code
        mkt_cap = ref.get("ref_mkt_cap_yi", 0)
        if mkt_cap > 0:
            df.loc[mask, "mkt_cap_yi"] = mkt_cap
            df.loc[mask, "mkt_cap_tier"] = get_mkt_cap_tier(mkt_cap)
            return True
        return False

    codes = df["code"].unique().tolist()
    health["total"] = len(codes)

    failed_codes = []
    for i, code in enumerate(codes):
        try:
            proxy_url = _get_qg_proxy()
            _set_requests_proxy(proxy_url)
        except Exception:
            pass
        try:
            secid = secid_from_code(code)
            d = _try_fetch(secid)
            if d:
                mkt_cap_yi = d["f116"] / 1e8
                mask = df["code"] == code
                df.loc[mask, "mkt_cap_yi"] = mkt_cap_yi
                df.loc[mask, "mkt_cap_tier"] = get_mkt_cap_tier(mkt_cap_yi)
                health["live"] += 1
            else:
                failed_codes.append(code)
        except Exception:
            failed_codes.append(code)
        _time.sleep(EASTMONEY_DELAY)

    # Retry pass
    if failed_codes:
        eprint(f"     Retrying {len(failed_codes)} failed stocks with fresh proxies...")
        still_failed = []
        for code in failed_codes:
            try:
                secid = secid_from_code(code)
                d = _try_fetch(secid, max_retries=3)
                if d:
                    mkt_cap_yi = d["f116"] / 1e8
                    mask = df["code"] == code
                    df.loc[mask, "mkt_cap_yi"] = mkt_cap_yi
                    df.loc[mask, "mkt_cap_tier"] = get_mkt_cap_tier(mkt_cap_yi)
                    health["live"] += 1
                else:
                    still_failed.append(code)
            except Exception:
                still_failed.append(code)
            _time.sleep(EASTMONEY_DELAY)
        failed_codes = still_failed

    # Static fallback for remaining failures
    if failed_codes:
        eprint(f"     ⚠️ Eastmoney FAILED for {len(failed_codes)} stocks: {failed_codes}")
        eprint(f"     Applying STATIC_REFERENCE fallback...")
        for code in failed_codes:
            if _apply_static_fallback(code):
                health["fallback"] += 1
            else:
                health["failed"] += 1

    # Health
    live_pct = health["live"] / health["total"] if health["total"] > 0 else 0
    if live_pct < 0.3:
        health["status"] = "error"
        eprint(f"     🔴 Eastmoney mkt-cap: {health['live']}/{health['total']} live, {health['fallback']} fallback, {health['failed']} failed")
    elif live_pct < 0.7:
        health["status"] = "warn"
        eprint(f"     🟡 Eastmoney mkt-cap: {health['live']}/{health['total']} live, {health['fallback']} fallback, {health['failed']} failed")
    else:
        eprint(f"     ✅ Eastmoney mkt-cap: {health['live']}/{health['total']} live, {health['fallback']} fallback")

    return df, health


# Backward-compatible wrapper (used by old caller + backtest)
def enrich_eastmoney(df):
    """Full enrichment (industry + market cap). See split functions above."""
    df, ind_health = enrich_industry_from_tushare(df)
    df, mkt_health = enrich_market_cap_from_eastmoney(df)
    health = mkt_health.copy()
    health["industry_source"] = ind_health
    return df, health

# ============================================================
# Phase 4: Price Data & Event Study
# ============================================================

def fetch_price_data(codes, start_date="20260401", end_date=None):
    """Batch fetch OHLCV from fmdata. Returns dict: code->DataFrame, plus __benchmark__."""
    if end_date is None: end_date = TODAY_STR
    eprint(f"     Fetching price data: {len(codes)} stocks ({start_date}~{end_date})...")

    price_cache, session = {}, requests.Session()
    for code in codes:
        try:
            url = f"http://127.0.0.1:1934/market/stock-daily?code={code}&start={start_date}&end={end_date}"
            r = session.get(url, timeout=30)
            r.raise_for_status()
            js = r.json()
            if js.get("data"):
                rows = [{"trade_date": int(it.get("trade_date", 0)),
                         "open": float(it.get("open", np.nan)),
                         "high": float(it.get("high", np.nan)),
                         "low": float(it.get("low", np.nan)),
                         "close": float(it.get("close", np.nan)),
                         "pre_close": float(it.get("pre_close", np.nan)),
                         "pct_chg": float(it.get("pct_chg", np.nan)),
                         "vol": float(it.get("vol", np.nan)),
                         "amount": float(it.get("amount", np.nan))}
                        for it in js["data"]]
                price_cache[code] = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
        except Exception:
            pass

    # Benchmark (CSI 300)
    try:
        url = f"http://127.0.0.1:1934/market/stock-daily?code=000300&start={start_date}&end={end_date}"
        r = session.get(url, timeout=30)
        js = r.json()
        if js.get("data"):
            rows = [{"trade_date": int(it.get("trade_date", 0)),
                     "close": float(it.get("close", np.nan))}
                    for it in js["data"]]
            price_cache["__benchmark__"] = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    except Exception:
        pass

    session.close()
    has_bm = "__benchmark__" in price_cache
    n_stocks = len(price_cache) - 1 if has_bm else len(price_cache)
    eprint(f"     Price data: {n_stocks}/{len(codes)} stocks")
    return price_cache

def find_event_trading_day(notice_date, price_df):
    """Find first trading day >= notice_date. Returns (trade_date_int, index) or (None,None)."""
    if price_df is None or price_df.empty or pd.isna(notice_date): return None, None
    notice_int = int(pd.Timestamp(notice_date).strftime("%Y%m%d"))
    mask = price_df["trade_date"] >= notice_int
    if not mask.any(): return None, None
    idx = int(mask.idxmax())
    return int(price_df.at[idx, "trade_date"]), idx

def compute_event_metrics(notice_date, price_df, benchmark_df=None):
    """Compute event study metrics: T0 gap/intraday/volume, T+ post-event CAR, pre-CAR T-20 to T-1."""
    if price_df is None or price_df.empty: return {}
    t0_date, t0_idx = find_event_trading_day(notice_date, price_df)
    if t0_date is None or t0_idx is None: return {}
    if t0_idx < 1: return {}

    close_before = price_df.at[t0_idx - 1, "close"]
    open_t0 = price_df.at[t0_idx, "open"]
    close_t0 = price_df.at[t0_idx, "close"]
    vol_t0 = price_df.at[t0_idx, "vol"]
    pct_chg_t0 = price_df.at[t0_idx, "pct_chg"]

    if not close_before or close_before == 0: return {}

    # T=0 metrics
    open_gap_pct = (open_t0 - close_before) / close_before * 100 if open_t0 else np.nan
    intraday_pct = (close_t0 - open_t0) / open_t0 * 100 if open_t0 and open_t0 != 0 else np.nan
    day_return_pct = pct_chg_t0 if not np.isnan(pct_chg_t0) else ((close_t0 - close_before) / close_before * 100)

    # Volume ratio
    if t0_idx >= 21:
        avg_vol = price_df.iloc[t0_idx - 20:t0_idx]["vol"].mean()
    elif t0_idx >= 2:
        avg_vol = price_df.iloc[0:t0_idx]["vol"].mean()
    else:
        avg_vol = np.nan
    vol_ratio = vol_t0 / avg_vol if (avg_vol and avg_vol > 0) else np.nan

    # Post-event CAR
    def _cum(start_idx, n):
        end_idx = min(start_idx + n, len(price_df) - 1)
        if end_idx <= start_idx: return np.nan
        sc = price_df.at[start_idx, "close"]
        ec = price_df.at[end_idx, "close"]
        return (ec - sc) / sc * 100 if sc and sc != 0 else np.nan

    car_3d, car_5d, car_10d = _cum(t0_idx, 3), _cum(t0_idx, 5), _cum(t0_idx, 10)
    car_20d = _cum(t0_idx, 20)

    car_5d_abnormal = np.nan
    if benchmark_df is not None and not benchmark_df.empty:
        _, bm_t0_idx = find_event_trading_day(notice_date, benchmark_df)
        if bm_t0_idx is not None:
            def _bm_cum(start_idx, n):
                end_idx = min(start_idx + n, len(benchmark_df) - 1)
                if end_idx <= start_idx: return np.nan
                sc = benchmark_df.at[start_idx, "close"]
                ec = benchmark_df.at[end_idx, "close"]
                return (ec - sc) / sc * 100 if sc and sc != 0 else np.nan
            bm_5d = _bm_cum(bm_t0_idx, 5)
            car_5d_abnormal = car_5d - bm_5d if (not np.isnan(car_5d) and not np.isnan(bm_5d)) else np.nan

    # ---- Pre-announcement CAR (T-20 to T-1) ----
    pre_t_start = max(0, t0_idx - 20)
    pre_t_end = t0_idx - 1
    pre_car_raw = np.nan
    pre_car_abnormal = np.nan
    if pre_t_end > pre_t_start:
        pre_close_start = price_df.at[pre_t_start, "close"]
        pre_close_end = price_df.at[pre_t_end, "close"]
        if pre_close_start and pre_close_start != 0:
            pre_car_raw = (pre_close_end - pre_close_start) / pre_close_start * 100
    if benchmark_df is not None and not benchmark_df.empty and not np.isnan(pre_car_raw):
        _, bm_t0_idx = find_event_trading_day(notice_date, benchmark_df)
        if bm_t0_idx is not None:
            bm_pre_start = max(0, bm_t0_idx - 20)
            bm_pre_end = bm_t0_idx - 1
            if bm_pre_end > bm_pre_start:
                bm_sc = benchmark_df.at[bm_pre_start, "close"]
                bm_ec = benchmark_df.at[bm_pre_end, "close"]
                if bm_sc and bm_sc != 0:
                    pre_car_abnormal = pre_car_raw - (bm_ec - bm_sc) / bm_sc * 100
    if np.isnan(pre_car_abnormal):
        pre_car_abnormal = pre_car_raw

    # Event days available after T0
    ev_days = len(price_df) - t0_idx - 1

    return {
        "open_gap_pct": round(open_gap_pct, 2) if not np.isnan(open_gap_pct) else np.nan,
        "intraday_pct": round(intraday_pct, 2) if not np.isnan(intraday_pct) else np.nan,
        "day_return_pct": round(day_return_pct, 2) if not np.isnan(day_return_pct) else np.nan,
        "vol_ratio": round(vol_ratio, 2) if not np.isnan(vol_ratio) else np.nan,
        "car_3d": round(car_3d, 2) if not np.isnan(car_3d) else np.nan,
        "car_5d": round(car_5d, 2) if not np.isnan(car_5d) else np.nan,
        "car_10d": round(car_10d, 2) if not np.isnan(car_10d) else np.nan,
        "car_20d": round(car_20d, 2) if not np.isnan(car_20d) else np.nan,
        "car_5d_abnormal": round(car_5d_abnormal, 2) if not np.isnan(car_5d_abnormal) else np.nan,
        "pre_car_raw": round(pre_car_raw, 2) if not np.isnan(pre_car_raw) else np.nan,
        "pre_car_abnormal": round(pre_car_abnormal, 2) if not np.isnan(pre_car_abnormal) else np.nan,
        "event_days_available": ev_days,
    }

def compute_all_event_metrics(df, price_cache):
    """Compute event study metrics for all stocks, pre-CAR too."""
    eprint("[5/5] Computing event study metrics (incl. pre-CAR)...")
    bm_df = price_cache.get("__benchmark__")
    cols = ["open_gap_pct","intraday_pct","day_return_pct","vol_ratio",
            "car_3d","car_5d","car_10d","car_20d","car_5d_abnormal",
            "pre_car_raw","pre_car_abnormal","event_days_available"]
    for c in cols: df[c] = np.nan

    count = 0
    for idx, row in df.iterrows():
        code, notice = row["code"], row.get("notice_date")
        if pd.isna(notice): continue
        pdf = price_cache.get(code)
        if pdf is None or pdf.empty: continue
        m = compute_event_metrics(notice, pdf, bm_df)
        if m:
            for k, v in m.items(): df.at[idx, k] = v
            count += 1

    eprint(f"  Event metrics: {count}/{len(df)} stocks")
    return df

# ============================================================
# Phase 5: Fund Flow Proxy (v3.4) + Rank Momentum
# ============================================================

def compute_fund_flow_proxy(df_noevent, window=5):
    """Fetch 主力资金流 as Price Confirm proxy for stocks without price events.
    Uses akshare stock_individual_fund_flow per-stock. Returns dict: code→rank-pct score."""
    if df_noevent.empty: return {}
    import akshare as ak
    flow_scores = {}
    codes = df_noevent["code"].tolist()
    for i, code in enumerate(codes):
        code_s = str(code).zfill(6)
        try:
            market = "sh" if code_s[0] == "6" else "sz" if code_s[0] in ("0","3") else None
            if not market: continue
            df_ff = ak.stock_individual_fund_flow(stock=code_s, market=market)
            if df_ff is None or len(df_ff) < window: continue
            recent = df_ff.tail(window)
            main_pct = recent['主力净流入-净占比'].mean()
            super_pct = recent['超大单净流入-净占比'].mean()
            flow_scores[code] = 0.6 * main_pct + 0.4 * super_pct
        except Exception:
            continue
    if not flow_scores: return {}
    vals = pd.Series(flow_scores)
    ranked = vals.rank(pct=True) * 100.0
    return {c: float(ranked[c]) for c in codes if c in flow_scores}


def compute_rank_momentum(df):
    """Track rank changes vs previous run. Saved to _prev.csv for next run."""
    df["rank_momentum"] = ""
    df["_rank_delta"] = np.nan
    hist_path = OUT_CSV.parent / "semiannual_investment_prev.csv"
    if not Path(hist_path).exists(): return df
    try:
        prev = pd.read_csv(hist_path, dtype={'code': str}, encoding='utf-8-sig')
        prev_map = dict(zip(prev["code"], prev["score_rank"]))
        for idx, row in df.iterrows():
            code = row["code"]
            if code in prev_map:
                delta = prev_map[code] - row["score_rank"]
                df.at[idx, "_rank_delta"] = delta
                df.at[idx, "rank_momentum"] = "📈" if delta > 5 else ("📉" if delta < -5 else "➡️")
            else:
                df.at[idx, "rank_momentum"] = "🆕"
    except Exception: pass
    return df


def save_rank_history(df):
    """Save snapshot for next run's rank momentum."""
    try:
        path = OUT_CSV.parent / "semiannual_investment_prev.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
    except Exception: pass


def compute_daily_pseudo_ic(df):
    """Walk-Forward Tier 1: Daily pseudo-IC during earnings season (v3.4).
    For stocks with day_return_pct available:
      - score = total_score (0-100)
      - return = day_return_pct (announcement-day return)
    Computes Spearman rank IC and Top5/Bot5 return spread.

    Returns dict with validation metrics, or None if insufficient data.
    """
    valid = df[df["day_return_pct"].notna() & df["total_score"].notna()]
    if len(valid) < 5:
        return None

    scores = valid["total_score"].values
    returns = valid["day_return_pct"].values.astype(float)

    # Spearman rank IC
    import scipy.stats as scs
    try:
        ic, p_val = scs.spearmanr(scores, returns)
    except Exception:
        return None

    n = len(valid)
    # T-stat for IC significance
    t_stat = ic * np.sqrt(n - 2) / np.sqrt(1 - ic**2) if abs(ic) < 1 else 0
    from scipy.stats import t as t_dist
    p_value = 2 * t_dist.sf(abs(t_stat), n - 2) if t_stat != 0 else 1

    # Top5 vs Bottom5
    valid_sorted = valid.sort_values("total_score", ascending=False)
    top5 = valid_sorted.head(5)["day_return_pct"].mean()
    bot5 = valid_sorted.tail(5)["day_return_pct"].mean()

    # Hit rate: score > median → positive return?
    median_score = valid["total_score"].median()
    high_score = valid[valid["total_score"] > median_score]
    hit_rate = (high_score["day_return_pct"] > 0).mean()

    return {
        "daily_ic": round(ic, 4),
        "n_obs": n,
        "t_stat": round(t_stat, 2),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "top5_avg": round(float(top5), 1),
        "bot5_avg": round(float(bot5), 1),
        "hit_rate": round(float(hit_rate), 3),
    }


# ============================================================
# Phase 5.5: Data Validation Gate
# ============================================================

def validate_data(df, data_health):
    """Pre-scoring data integrity validation.

    Runs after all enrichment, before scoring. Critical failures block report generation.
    Returns dict with keys: block (bool), checks (list of per-check dicts), summary (str).
    """
    checks = []
    total = len(df)
    n_q1 = int(df["q1_profit_yi"].notna().sum())
    n_fc = int(df.get("forecast_mid_yi", pd.Series(dtype=float)).notna().sum())
    n_mkt = int(df.get("mkt_cap_yi", pd.Series(dtype=float)).notna().sum())
    n_ind = int((df.get("industry", "") != "").sum())
    n_price = int(df.get("open_gap_pct", pd.Series(dtype=float)).notna().sum())

    # ---- CRITICAL checks ----
    # C1: Duplicate codes
    dup_mask = df.duplicated(subset=["code"], keep=False)
    if dup_mask.any():
        dup_codes = df.loc[dup_mask, "code"].unique().tolist()
        checks.append({"name": "duplicate_codes", "level": "critical", "passed": False,
                       "detail": f"重复股票: {dup_codes}"})
    else:
        checks.append({"name": "duplicate_codes", "level": "critical", "passed": True,
                       "detail": "ok"})

    # C2: Insufficient sample
    if total < 5:
        checks.append({"name": "insufficient_stocks", "level": "critical", "passed": False,
                       "detail": f"仅{total}只标的(需≥5)"})
    else:
        checks.append({"name": "insufficient_stocks", "level": "critical", "passed": True,
                       "detail": f"ok({total})"})

    # C3: Q1 coverage (foundational for SUE/EQ)
    q1_pct = n_q1 / total if total > 0 else 0
    if q1_pct < 0.7:
        checks.append({"name": "q1_coverage", "level": "critical", "passed": False,
                       "detail": f"Q1覆盖率{q1_pct:.0%}({n_q1}/{total})<70%"})
    else:
        checks.append({"name": "q1_coverage", "level": "critical", "passed": True,
                       "detail": f"ok({q1_pct:.0%})"})

    # C4: Forecast coverage (primary signal)
    fc_pct = n_fc / total if total > 0 else 0
    if fc_pct < 0.5:
        checks.append({"name": "forecast_coverage", "level": "critical", "passed": False,
                       "detail": f"预告覆盖率{fc_pct:.0%}({n_fc}/{total})<50%"})
    else:
        checks.append({"name": "forecast_coverage", "level": "critical", "passed": True,
                       "detail": f"ok({fc_pct:.0%})"})

    # ---- WARNING checks ----
    # W1: Forecast type vs value consistency
    mismatch_codes = []
    for _, row in df.iterrows():
        ft = str(row.get("forecast_type", ""))
        mid = row.get("forecast_mid_yi")
        prior = row.get("h1_prior_profit_yi")
        if pd.isna(mid) or pd.isna(prior):
            continue
        if ft in ("预增", "大增", "扭亏") and mid <= prior:
            mismatch_codes.append(f"{row['code']}:{ft} mid={mid:.2f}≤prior={prior:.2f}")
        elif ft in ("预减", "略减", "首亏", "续亏") and mid >= prior and ft not in ("续亏",):
            mismatch_codes.append(f"{row['code']}:{ft} mid={mid:.2f}≥prior={prior:.2f}")
    if mismatch_codes:
        checks.append({"name": "forecast_type_mismatch", "level": "warning", "passed": False,
                       "detail": f"预告类型矛盾({len(mismatch_codes)}): {mismatch_codes[:5]}"})
    else:
        checks.append({"name": "forecast_type_mismatch", "level": "warning", "passed": True,
                       "detail": "ok"})

    # W2: Forecast range flip (lower > upper)
    range_flip = 0
    for _, row in df.iterrows():
        lo = row.get("forecast_lower_yi"); hi = row.get("forecast_upper_yi")
        if pd.notna(lo) and pd.notna(hi) and lo > hi:
            range_flip += 1
    if range_flip > 0:
        checks.append({"name": "forecast_range_flip", "level": "warning", "passed": False,
                       "detail": f"预告区间翻转({range_flip}只)"})
    else:
        checks.append({"name": "forecast_range_flip", "level": "warning", "passed": True,
                       "detail": "ok"})

    # W3: Q2 implied outlier
    outlier_count = 0
    for _, row in df.iterrows():
        q2i = row.get("q2_profit_yi_implied")
        rev = row.get("q1_revenue_yi")
        if pd.isna(q2i):
            continue
        if q2i < -500:
            outlier_count += 1
        elif pd.notna(rev) and rev > 0 and q2i > rev * 3:
            outlier_count += 1
    if outlier_count > 0:
        checks.append({"name": "q2_implied_outlier", "level": "warning", "passed": False,
                       "detail": f"Q2隐含值异常({outlier_count}只)"})
    else:
        checks.append({"name": "q2_implied_outlier", "level": "warning", "passed": True,
                       "detail": "ok"})

    # W4: Market cap coverage
    mkt_pct = n_mkt / total if total > 0 else 0
    if mkt_pct < 0.8:
        checks.append({"name": "mkt_cap_coverage", "level": "warning", "passed": False,
                       "detail": f"市值覆盖率{mkt_pct:.0%}({n_mkt}/{total})<80%"})
    else:
        checks.append({"name": "mkt_cap_coverage", "level": "warning", "passed": True,
                       "detail": f"ok({mkt_pct:.0%})"})

    # W5: Industry coverage
    ind_pct = n_ind / total if total > 0 else 0
    if ind_pct < 0.8:
        checks.append({"name": "industry_coverage", "level": "warning", "passed": False,
                       "detail": f"行业覆盖率{ind_pct:.0%}({n_ind}/{total})<80%"})
    else:
        checks.append({"name": "industry_coverage", "level": "warning", "passed": True,
                       "detail": f"ok({ind_pct:.0%})"})

    # W6: Price coverage
    price_pct = n_price / total if total > 0 else 0
    if price_pct < 0.5:
        checks.append({"name": "price_coverage", "level": "warning", "passed": False,
                       "detail": f"价格覆盖率{price_pct:.0%}({n_price}/{total})<50%"})
    else:
        checks.append({"name": "price_coverage", "level": "warning", "passed": True,
                       "detail": f"ok({price_pct:.0%})"})

    # ---- Aggregate ----
    critical_fails = [c for c in checks if c["level"] == "critical" and not c["passed"]]
    warning_fails = [c for c in checks if c["level"] == "warning" and not c["passed"]]
    block = len(critical_fails) > 0

    # Build summary line
    n_crit_pass = len([c for c in checks if c["level"] == "critical" and c["passed"]])
    n_crit_total = len([c for c in checks if c["level"] == "critical"])
    n_warn_pass = len([c for c in checks if c["level"] == "warning" and c["passed"]])
    n_warn_total = len([c for c in checks if c["level"] == "warning"])

    if block:
        status_icon = "🔴"
    elif warning_fails:
        status_icon = "⚠️"
    else:
        status_icon = "✅"

    detail_parts = []
    for c in checks:
        detail_parts.append(f"{c['name']}={c['detail']}")
    detail_str = "; ".join(detail_parts)

    summary = f"{status_icon} 关键{n_crit_pass}/{n_crit_total} 警告{n_warn_pass}/{n_warn_total}"
    summary_line = f"# VALIDATION: {status_icon} crit={n_crit_pass}/{n_crit_total} warn={n_warn_pass}/{n_warn_total} | {detail_str}"

    result = {
        "block": block,
        "checks": checks,
        "summary": summary,
        "summary_line": summary_line,
        "critical_fails": critical_fails,
        "warning_fails": warning_fails,
    }

    # Print diagnostics
    if block:
        eprint(f"  🔴 DATA VALIDATION FAILED ({len(critical_fails)} critical):")
        for cf in critical_fails:
            eprint(f"     [{cf['name']}] {cf['detail']}")
    if warning_fails:
        eprint(f"  🟡 Data warnings ({len(warning_fails)}):")
        for wf in warning_fails:
            eprint(f"     [{wf['name']}] {wf['detail']}")
    if not block and not warning_fails:
        eprint(f"  ✅ Data validation passed ({n_crit_total}+{n_warn_total} checks)")

    return result


# ============================================================
# Phase 6: Scoring (v3.4)
# ============================================================

def compute_scores(df, phase=None):
    """Compute 5-factor rank-percentile scores (v3.0 - DeerFlow audit).
    Rank-percentile: robust for N=22, immune to outliers, investor-intuitive (85=better than 85% peers).

    Factors:
      1. SUE proxy (20%): implied H1 growth vs last year, confidence-weighted
      2. Q1 Earnings Quality (15%): accrual proxy + revenue growth + Q1 profitability
      3. Price Confirm (15%): announcement-day market reaction
      4. Pre-CAR Inverse (20%): -1× pre-announcement abnormal return
      5. Industry Momentum (15%): SW sector 20d relative return
      6. Announcement Timing (15%): earlier = stronger signal (A股实证)

    Phase 1 (全预告): SUE↓, EQ↑, Price↓, PreCAR↑
    Phase 2 (部分快报): SUE↑, EQ↓
    Phase 3 (正式报): SUE↑↑, EQ↓
    """
    if phase is None: phase = detect_earnings_phase(df)
    eprint(f"     Computing scores (phase={phase})...")

    # --- Dynamic weights per phase ---
    phase_weights = {
        "PHASE_1_PREVIEW": {"sue": 0.20, "eq": 0.15, "price": 0.15, "precar": 0.20, "industry": 0.15, "timing": 0.15},
        "PHASE_2_PARTIAL": {"sue": 0.25, "eq": 0.15, "price": 0.15, "precar": 0.15, "industry": 0.15, "timing": 0.15},
        "PHASE_3_FULL":    {"sue": 0.30, "eq": 0.20, "price": 0.10, "precar": 0.05, "industry": 0.15, "timing": 0.00, "flow": 0.20},
    }
    W = phase_weights.get(phase, phase_weights["PHASE_1_PREVIEW"])

    # SUE confidence weights (DeerFlow v3.1): seasonal proxy > growth proxy > type-only
    sue_conf = {"seasonal": 0.85, "growth_proxy": 0.55, "type_only": 0.20, "actual": 1.0, "none": 0.20}

    # --- Factor 1: SUE proxy (rank-percentile, confidence-weighted) ---
    df["sue_score"] = rank_pct_neutral(df["sue_raw"].fillna(0), ascending=True)
    # Apply SUE confidence weighting: unreliable SUE → score shrunk toward 50
    for idx, row in df.iterrows():
        method = str(row.get("sue_method", "none"))
        conf = sue_conf.get(method, 0.5)
        if conf < 1.0:
            df.at[idx, "sue_score"] = 50.0 + (df.at[idx, "sue_score"] - 50.0) * conf
    df["sue_confidence"] = df["sue_method"].apply(lambda m: sue_conf.get(str(m), 0.5))

    # --- Factor 2: Q1 Earnings Quality (DeerFlow v3.1) ---
    df = compute_earnings_quality(df)

    # --- Factor 3: Price Confirmation (v3.4: fund flow proxy for no-event stocks) ---
    df["price_raw"] = (df["open_gap_pct"].fillna(0) * 0.5 + df["intraday_pct"].fillna(0) * 0.3)
    vol_adj = df["vol_ratio"].fillna(1).clip(0.1, 10).apply(lambda x: np.log(x) * 2)
    df["price_raw"] = df["price_raw"] + vol_adj * 0.2
    df["price_score"] = rank_pct_neutral(df["price_raw"], ascending=True)
    no_event = df["open_gap_pct"].isna()
    df.loc[no_event, "price_score"] = 50.0  # neutral default

    # --- DeerFlow v3.4: Fund flow proxy for stocks without price events ---
    fund_flow_data = compute_fund_flow_proxy(df[no_event]) if no_event.sum() > 0 else {}
    if fund_flow_data:
        n_flow = 0
        for idx in df[no_event].index:
            code = df.at[idx, "code"]
            if code in fund_flow_data:
                ff_score = fund_flow_data[code]
                # Blend: 50% Bayesian prior + 50% fund flow signal
                df.at[idx, "price_score"] = 50.0 * 0.5 + ff_score * 0.5
                df.at[idx, "_has_price"] = 2  # mark as "fund flow proxy"
                n_flow += 1
        eprint(f"     Fund flow proxy: {n_flow}/{no_event.sum()} stocks filled")

    # --- Factor 3: Pre-CAR Inverse (higher pre-CAR → higher risk → lower score) ---
    df["precar_score"] = rank_pct_neutral(df["pre_car_abnormal"].fillna(0) * -1, ascending=True)
    no_precar = df["pre_car_abnormal"].isna()
    df.loc[no_precar, "precar_score"] = 50.0

    # --- Factor 4: Industry Momentum (SW sector 20d relative return) ---
    df = compute_industry_momentum(df)

    # --- Factor 5: Announcement Timing (earlier=better) ---
    df = compute_timing_factor(df)

    # --- Volume Anomaly: PEAD modulator (bonus/malus applied to Price score) ---
    df = compute_volume_anomaly(df)

    # --- Composite Score v3.2 (DeerFlow: weight redistribution + interaction + veto) ---

    # Mark factor availability
    df["_has_price"] = df["open_gap_pct"].notna().astype(int)
    df["_has_precar"] = df["pre_car_abnormal"].notna().astype(int)
    df["_has_industry"] = (df["industry_score"] != 50.0).astype(int)  # industry momentum matched
    df["_sue_effective"] = (df["sue_confidence"] >= 0.5).astype(int)

    # Base weighted score (with weight redistribution for missing factors)
    factor_scores = {
        "sue": df["sue_score"].values,
        "eq": df["eq_score"].values,
        "price": df["price_score"].values,
        "precar": df["precar_score"].values,
        "industry": df["industry_score"].values,
        "timing": df["timing_score"].values,
    }
    factor_valid = {
        "sue": df["_sue_effective"].values,
        "eq": np.ones(len(df)),  # always valid
        "price": df["_has_price"].values,
        "precar": df["_has_precar"].values,
        "industry": df["_has_industry"].values,
        "timing": np.ones(len(df)),  # always valid
    }

    scores_raw = np.zeros(len(df))
    coverage_ratios = np.zeros(len(df))
    for i in range(len(df)):
        valid_factors = {k: v[i] for k, v in factor_valid.items() if v[i] == 1}
        if not valid_factors:
            scores_raw[i] = 50.0
            coverage_ratios[i] = 0.0
            continue

        # Redistribute weights from invalid to valid factors
        released = sum(W[k] for k in W if k not in valid_factors)
        valid_weight_sum = sum(W[k] for k in valid_factors)
        adjusted_weights = {}
        for k in valid_factors:
            adjusted_weights[k] = W[k] + (W[k] / valid_weight_sum) * released

        score = sum(factor_scores[k][i] * adjusted_weights[k] for k in valid_factors)
        scores_raw[i] = score
        coverage_ratios[i] = len(valid_factors) / len(W)

    # Coverage discount: Bayesian shrinkage (v3.3) — continuous, asymmetric
    # Low coverage → shrink toward mean (50), not fixed penalty
    # High-scoring low-coverage stocks shrink MORE than low-scoring ones
    coverage_shrinkage = np.where(
        coverage_ratios >= 0.9, 0.0,
        np.where(coverage_ratios >= 0.75, 0.15,   # 5/6
        np.where(coverage_ratios >= 0.65, 0.30,   # 4/6
        0.45)))  # 3/6 or fewer
    coverage_adjusted = scores_raw * (1 - coverage_shrinkage) + 50.0 * coverage_shrinkage
    df["_coverage_ratio"] = coverage_ratios
    df["_coverage_penalty"] = coverage_adjusted - scores_raw  # log for diagnostics

    # --- Interaction v3.3: Continuous functions (no discrete thresholds) ---
    # Earnings trap: SUE↑ × EQ↓ → penalty (max -10)
    # Quality confirmation: SUE↑ × EQ↑ → bonus (max +6)
    interaction_bonus = np.zeros(len(df))
    sue_scores = df["sue_score"].values
    eq_scores = df["eq_score"].values
    for i in range(len(df)):
        sue_excess = max(0, sue_scores[i] - 50) / 50  # 50-100 → 0-1
        eq_weakness = np.clip((55 - eq_scores[i]) / 40, 0, 1)  # EQ<55 → 0-1
        eq_strength = np.clip((eq_scores[i] - 55) / 40, 0, 1)

        # Earnings trap: high SUE + low EQ
        trap = (sue_excess * eq_weakness) ** 0.7 * 10.0
        # Quality confirmation: high SUE + high EQ
        confirm = (sue_excess * eq_strength) ** 0.7 * 6.0
        # Quality-reversal signal: low SUE + high EQ (turnaround potential)
        sue_low = max(0, (45 - sue_scores[i]) / 45)
        reversal = (sue_low * eq_strength) ** 0.6 * 3.0

        interaction_bonus[i] = confirm - trap + reversal

    # PreCAR×SUE price-in risk (continuous, v3.3)
    precar_penalty = np.zeros(len(df))
    for i in range(len(df)):
        if df["_has_precar"].values[i]:
            sue_high = max(0, sue_scores[i] - 50) / 50
            precar_low = np.clip((35 - df["precar_score"].values[i]) / 35, 0, 1)
            precar_penalty[i] = -(sue_high * precar_low) ** 0.7 * 6.0

    df["_interaction_bonus"] = interaction_bonus

    # --- Factor veto gates v3.3: weighted weakness replaces MULTI_LOW count ---
    df["_veto_flags"] = ""
    for i in range(len(df)):
        flags = []
        if df["eq_score"].values[i] < 15: flags.append("EQ_VETO")
        if df["_has_precar"].values[i] and df["precar_score"].values[i] < 15: flags.append("PRECAR_VETO")
        if df["timing_score"].values[i] < 10: flags.append("TIMING_VETO")

        # Weighted weakness: MULTI_LOW counting (34.5% = random noise) →
        # weight sum of factor weakness, only triggers when >25% of weight is in sub-20 factors
        weak_sum = 0
        factor_w = {"sue":0.20,"eq":0.15,"price":0.15,"precar":0.20,"industry":0.15,"timing":0.15}
        factor_v = {"sue":sue_scores[i],"eq":eq_scores[i],"price":df["price_score"].values[i],
                     "precar":df["precar_score"].values[i],"industry":df["industry_score"].values[i],
                     "timing":df["timing_score"].values[i]}
        for k, w in factor_w.items():
            if factor_v[k] < 20:
                weak_sum += w * (1 - factor_v[k] / 20)
        if weak_sum > 0.25:
            flags.append("WEAK_WEIGHTED")

        df.at[i, "_veto_flags"] = ",".join(flags) if flags else ""

    # Volume anomaly bonus/malus
    df["vol_bonus"] = (df["vol_anomaly_z"] * 0.05 * df["price_score"]).clip(-5, 5).fillna(0)

    # --- Final composite v3.3 ---
    df["total_score"] = coverage_adjusted + df["vol_bonus"].values + interaction_bonus + precar_penalty
    df["total_score"] = df["total_score"].clip(0, 100)

    df["score_rank"] = df["total_score"].rank(ascending=False, method="min").fillna(999).astype(int)

    # Round
    for c in ["sue_score","eq_score","price_score","precar_score","industry_score","timing_score","total_score"]:
        df[c] = df[c].round(1)

    top = df["total_score"].max()
    med = df["total_score"].median()
    bot = df["total_score"].min()
    stdv = df["total_score"].std()
    veto_count = int((df["_veto_flags"] != "").sum())
    eprint(f"     Scores: range {bot:.1f}-{top:.1f}, median={med:.1f}, std={stdv:.1f} (phase={phase}, veto={veto_count})")
    return df


def detect_earnings_phase(df):
    """Auto-detect earnings season phase based on data coverage."""
    has_h1_report = df["h1_profit_yi_actual"].notna().sum()
    has_express = (df["source"] == "快报").sum()
    total = len(df)
    coverage = (has_h1_report + has_express) / total if total > 0 else 0
    if coverage < 0.1:
        return "PHASE_1_PREVIEW"
    elif coverage < 0.5:
        return "PHASE_2_PARTIAL"
    else:
        return "PHASE_3_FULL"


def fetch_sw_industry_returns():
    """Fetch 申万一级行业近20日涨跌幅 from fmdata sw-close endpoint.
    Format: [{date: ..., 'industry1': value, 'industry2': value, ...}, ...]"""
    import requests as _r
    try:
        resp = _r.get("http://127.0.0.1:1934/market/sw-close", timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        if isinstance(raw, dict) and "data" in raw:
            rows = raw["data"]
        elif isinstance(raw, list):
            rows = raw
        else:
            return {}

        if not rows:
            return {}

        # Parse: each row is {date: ..., industry1: value, ...}
        df_sw = pd.DataFrame(rows)
        date_col = next((c for c in df_sw.columns if c in ("date", "trade_date")), df_sw.columns[0])
        df_sw["date"] = pd.to_datetime(df_sw[date_col])
        df_sw = df_sw.sort_values("date")

        # Get all industry columns (non-date)
        ind_cols = [c for c in df_sw.columns if c not in ("date", "trade_date", date_col)]
        if len(df_sw) < 20:
            return {}

        result = {}
        for ind in ind_cols:
            # Both "化工" and "化学制品" are possible industry names from eastmoney
            # The stock's industry field may not match SW industry names exactly
            if df_sw[ind].notna().sum() >= 20:
                ret_20d = (df_sw[ind].iloc[-1] / df_sw[ind].iloc[-20] - 1) * 100
                result[ind] = ret_20d
        return result
    except Exception as e:
        eprint(f"  WARNING: SW industry return fetch failed: {e}")
        return {}


def compute_industry_momentum(df):
    """Compute industry momentum factor (rank-percentile of SW sector 20d return).
    Maps stock's eastmoney industry name → SW 申万一级行业 via fuzzy matching + mapping table."""
    sw_returns = fetch_sw_industry_returns()
    df["industry_score"] = 50.0
    df["industry_momentum_pct"] = np.nan

    if not sw_returns:
        eprint("     Industry momentum: SW data unavailable → all neutral 50")
        return df

    # Eastmoney industry → SW industry mapping (申万一级)
    # Most eastmoney sub-industries map to these SW level-1 sectors
    sw_name_list = list(sw_returns.keys())
    eprint(f"     SW industries available: {len(sw_name_list)}")

    for idx, row in df.iterrows():
        ind = str(row.get("industry", "")).strip()
        if not ind: continue

        # Direct match with SW industry name
        if ind in sw_returns:
            df.at[idx, "industry_momentum_pct"] = sw_returns[ind]
            continue

        # Try fuzzy: check if SW name contains stock industry, or use a quick substring
        matched = None
        for sw_name in sw_name_list:
            if sw_name in ind or ind in sw_name:
                matched = sw_name
                break
        if matched:
            df.at[idx, "industry_momentum_pct"] = sw_returns[matched]
            continue

        # Keyword-based mapping for common eastmoney sub-industries
        keyword_map = {
            "半导体": "电子", "芯片": "电子", "集成电路": "电子",
            "消费电子": "电子", "光学光电子": "电子",
            "化学": "化工", "化工": "化工", "化肥": "化工",
            "汽车": "汽车",
            "医药": "医药生物", "制药": "医药生物", "生物": "医药生物",
            "电力": "公用事业", "环保": "公用事业", "公用": "公用事业",
            "钢铁": "钢铁",
            "煤炭": "煤炭",
            "银行": "银行",
            "非银": "非银金融", "保险": "非银金融", "证券": "非银金融",
            "房地产": "房地产",
            "建筑": "建筑装饰", "建材": "建筑材料", "水泥": "建筑材料",
            "机械": "机械设备", "设备": "机械设备",
            "食品": "食品饮料", "酒": "食品饮料", "饮料": "食品饮料",
            "纺织": "纺织服饰", "服装": "纺织服饰",
            "计算机": "计算机",
            "通信": "通信",
            "传媒": "传媒",
            "国防": "国防军工", "军工": "国防军工",
            "有色金属": "有色金属", "黄金": "有色金属", "贵金属": "有色金属",
            "新能源": "电力设备", "光伏": "电力设备", "风电": "电力设备",
            "电池": "电力设备", "锂电": "电力设备", "储能": "电力设备",
            "家电": "家用电器",
            "交通运输": "交通运输", "航空": "交通运输", "物流": "交通运输",
            "石油": "石油石化", "石化": "石油石化", "炼化": "石油石化",
            "金属": "有色金属", "新材料": "有色金属",
            "塑料": "化工", "包装": "轻工制造",
            "电网": "电力设备", "电气": "电力设备",
            "商贸": "商贸零售", "零售": "商贸零售",
            "社会服务": "社会服务", "教育": "社会服务",
        }

        for keyword, sw_name in keyword_map.items():
            if keyword in ind:
                if sw_name in sw_returns:
                    matched = sw_name
                    break

        if matched:
            df.at[idx, "industry_momentum_pct"] = sw_returns[matched]

    # Rank-percentile
    df["industry_score"] = rank_pct_neutral(df["industry_momentum_pct"], ascending=True)
    n_scored = int(df["industry_momentum_pct"].notna().sum())
    eprint(f"     Industry momentum: {n_scored}/{len(df)} stocks matched to SW sectors")
    return df


def compute_timing_factor(df):
    """Announcement timing: earlier disclosed = potentially stronger signal (A股实证).
    Compute days from earliest announcement in this batch → rank percentile."""
    df["timing_score"] = 50.0
    df["timing_rank_pct"] = np.nan

    valid = df[df["notice_date"].notna()]
    if len(valid) < 2: return df

    # Days since first announcement in batch
    earliest = valid["notice_date"].min()
    df["timing_delta_days"] = (df["notice_date"] - earliest).dt.days
    # Earlier = higher score
    df["timing_score"] = rank_pct_neutral(-df["timing_delta_days"].fillna(0), ascending=True)

    eprint(f"     Timing factor: range {df['timing_delta_days'].min():.0f}-{df['timing_delta_days'].max():.0f} days from first")
    return df


def compute_volume_anomaly(df):
    """Pre-announcement volume anomaly as PEAD modulator.
    High volume before announcement → info already leaked → PEAD weaker (penalty).
    Low volume before announcement → market unaware → PEAD stronger (bonus)."""
    df["vol_anomaly_z"] = 0.0
    df["vol_anomaly_label"] = ""

    # Use vol_ratio as proxy: vol_ratio > 2 = abnormal volume
    df.loc[df["vol_ratio"] > 3, "vol_anomaly_z"] = -1.0  # extreme volume → penalty
    df.loc[(df["vol_ratio"] > 2) & (df["vol_ratio"] <= 3), "vol_anomaly_z"] = -0.5
    df.loc[(df["vol_ratio"] < 0.5), "vol_anomaly_z"] = +0.5  # low volume → bonus
    df.loc[(df["vol_ratio"] > 0.5) & (df["vol_ratio"] <= 1.5), "vol_anomaly_z"] = +0.3

    df.loc[df["vol_ratio"].isna(), "vol_anomaly_z"] = 0.0

    n_vol = int(df["vol_ratio"].notna().sum())
    if n_vol > 0:
        eprint(f"     Volume anomaly: {n_vol} stocks assessed")
    return df


def compute_earnings_quality(df):
    """Q1 Earnings Quality factor (DeerFlow v3.1).
    3 sub-components from Q1 cjpy data:
      1. Accrual proxy (50%): -tanh((profit_growth - revenue_growth) * 0.03)
         Profit growing much faster than revenue = low quality (insufficient cash support)
      2. Revenue growth quality (30%): tanh(revenue_growth * 0.05)
         Revenue growth is the foundation of sustainable profit growth
      3. Q1 profitability direction (20%): sign(Q1_profit) × |Q1_profit_growth|
         Negative profit is a strong warning signal

    Sources: q1_rev_yoy_pct, q1_prof_yoy_pct, q1_profit_yi (all already from cjpy)
    """
    df["eq_accrual"] = 0.0
    df["eq_rev_growth"] = 0.0
    df["eq_profitability"] = 0.0

    # 1. Accrual proxy: tanh caps extreme values at ±1
    profit_rev_gap = df["q1_prof_yoy_pct"].fillna(0) - df["q1_rev_yoy_pct"].fillna(0)
    # Deviation from 1:1 profit-revenue growth = penalty (both extremes are bad)
    df["eq_accrual"] = 1.0 - np.abs(np.tanh(profit_rev_gap * 0.03))

    # 2. Revenue growth
    df["eq_rev_growth"] = np.tanh(df["q1_rev_yoy_pct"].fillna(0) * 0.05)

    # 3. Profitability: profit positive + growth direction
    prof = df["q1_profit_yi"].fillna(0)
    prof_growth = np.tanh(df["q1_prof_yoy_pct"].fillna(0) * 0.05)
    df["eq_profitability"] = 0.5 * np.sign(prof + 1e-8) + 0.5 * prof_growth

    # Composite EQ
    df["eq_raw"] = 0.50 * df["eq_accrual"] + 0.30 * df["eq_rev_growth"] + 0.20 * df["eq_profitability"]
    df["eq_score"] = rank_pct_neutral(df["eq_raw"], ascending=True)

    n_eq = int(df["eq_raw"].notna().sum())
    eprint(f"     EQ factor: {n_eq}/{len(df)} stocks scored")
    return df

def apply_filters(df):
    """Apply liquidity/quality filters. Marks is_filtered and filter_reason."""
    eprint("     Applying filters...")
    df["is_filtered"] = False
    df["filter_reason"] = ""
    for idx, row in df.iterrows():
        reasons = []
        mkt_cap = row.get("mkt_cap_yi")
        if not pd.isna(mkt_cap) and mkt_cap < FILTER_MIN_MKT_CAP_YI:
            reasons.append(f"low_mktcap={mkt_cap:.0f}yi")
        if reasons:
            df.at[idx, "is_filtered"] = True
            df.at[idx, "filter_reason"] = ";".join(reasons)
    n = int(df["is_filtered"].sum())
    eprint(f"     Filtered: {n}/{len(df)} stocks")
    return df

# ============================================================
# Phase 6: Report Generation (v2 format)
# ============================================================

def generate_report(df, phase=None, validation_result=None):
    """Generate v3.0 signal-funnel report (DeerFlow audit refactor).
    Compact, actionable: 3 signal tiers, stock-level logic, data confidence."""
    if phase is None: phase = detect_earnings_phase(df)
    eprint("     Generating v3.0 report...")
    out = []
    rpt_date = TODAY.strftime("%Y-%m-%d")
    last_td = LAST_TRADE_DAY.strftime("%Y-%m-%d")

    # Phase label + confidence
    phase_labels = {
        "PHASE_1_PREVIEW": "⚠️ 预告期(低置信度): SUE代理,建议仅观察不重仓",
        "PHASE_2_PARTIAL": "🟡 混合期(中置信度): 部分快报可查",
        "PHASE_3_FULL":    "🟢 财报期(高置信度): 真SUE已激活",
    }
    phase_label = phase_labels.get(phase, phase_labels["PHASE_1_PREVIEW"])

    out.append(f"# 半年报投资决策日报 — {rpt_date}")
    out.append(f"> 最后交易日: {last_td} | 覆盖: {len(df)}只 | {phase_label}")
    out.append(f"> 6因子 + 连续交互 + 贝叶斯收缩 + 资金流向Proxy + 日频验证 | v3.4 第五轮DeerFlow审计迭代")
    out.append("")

    # --- Signal Tier Classification ---
    # Apply veto: stocks with active veto are capped at "watch" tier regardless of score
    has_veto = df["_veto_flags"] != ""

    strong = df[(df["total_score"] >= 55) & (~df["is_filtered"]) & (~has_veto)].nlargest(10, "total_score")
    watch = df[((df["total_score"] >= 45) & (df["total_score"] < 55)) | (has_veto & (df["total_score"] >= 55))].nsmallest(999, "total_score")
    watch = watch[~watch["is_filtered"]].nlargest(20, "total_score")
    avoid = df[(df["total_score"] < 45) | (df["is_filtered"])].nsmallest(10, "total_score")

    # Data completeness counters
    n_q1 = int(df["q1_profit_yi"].notna().sum())
    n_price = int(df["open_gap_pct"].notna().sum())
    n_mkt = int(df["mkt_cap_yi"].notna().sum())
    n_industry = int((df.get("industry", "") != "").sum())

    out.append("---")

    # --- 🟢 Strong Signal Zone ---
    out.append(f"## 🟢 强信号区 (评分≥55, {len(strong)}只)")
    out.append("")
    if len(strong) > 0:
        out.append("| # | 代码 | 简称 | 评分 | 覆盖度 | 核心信号 | 警告 | 一句话逻辑 |")
        out.append("|---|------|------|------|--------|----------|------|-----------|")
        for _, r in strong.iterrows():
            code = str(r["code"]).zfill(6)
            name = r["name"]
            cov = f"{r.get('_coverage_ratio',1):.0%}"
            # Core signal summary
            signals = []
            if pd.notna(r.get("sue_score")) and r["sue_score"] > 65: signals.append("SUE↑")
            if pd.notna(r.get("eq_score")) and r["eq_score"] > 65: signals.append("EQ优质↑")
            if pd.notna(r.get("price_score")) and r["price_score"] > 65: signals.append("价格确认↑")
            if pd.notna(r.get("precar_score")) and r["precar_score"] > 65: signals.append("PreCAR安全↑")
            if pd.notna(r.get("industry_score")) and r["industry_score"] > 65: signals.append("行业顺风↑")
            if pd.notna(r.get("timing_score")) and r["timing_score"] > 65: signals.append("早披露")
            core = "+".join(signals[:3]) if signals else "--"

            # One-sentence logic
            h1yy = f"{r.get('h1_prof_forecast_yoy_pct'):+.0f}%" if pd.notna(r.get('h1_prof_forecast_yoy_pct')) else ""
            logic_parts = []
            if h1yy: logic_parts.append(f"H1{h1yy}")
            if pd.notna(r.get("q2_prof_implied_qoq_pct")): logic_parts.append(f"Q2QoQ{r['q2_prof_implied_qoq_pct']:.0f}%")
            logic = ", ".join(logic_parts) if logic_parts else "待分析"

            # Warnings
            warnings = []
            if r.get("_interaction_bonus", 0) < -3: warnings.append("⚠️盈余陷阱")
            veto = str(r.get("_veto_flags", ""))
            warn_str = ",".join(warnings) if warnings else (veto if veto else "")

            out.append(f"| {int(r['score_rank'])} | {code} | {name} | {r['total_score']:.1f} | {cov} | {core} | {warn_str} | {logic} |")
        out.append("")
    else:
        out.append("今日无强信号标的。")
        out.append("")

    # --- 🟡 Watch Zone ---
    out.append(f"## 🟡 观察区 (评分45-55, {len(watch)}只)")
    out.append("")
    if len(watch) > 0:
        out.append("| # | 代码 | 简称 | 评分 | H1YoY | Q2QoQ | 行业 | 关注理由 |")
        out.append("|---|------|------|------|-------|-------|------|----------|")
        for _, r in watch.head(15).iterrows():
            code = str(r["code"]).zfill(6)
            name = r["name"]
            h1yy = f"{r.get('h1_prof_forecast_yoy_pct'):+.0f}%" if pd.notna(r.get('h1_prof_forecast_yoy_pct')) else "--"
            q2qq = f"{r.get('q2_prof_implied_qoq_pct'):+.0f}%" if pd.notna(r.get('q2_prof_implied_qoq_pct')) else "--"
            ind = str(r.get("industry", ""))[:8] if pd.notna(r.get("industry")) else "--"
            reason = ""
            if pd.notna(r.get("price_score")) and r["price_score"] < 40: reason = "价格确认弱"
            elif pd.notna(r.get("precar_score")) and r["precar_score"] < 40: reason = "PreCAR风险"
            else: reason = "信号中性"
            veto = str(r.get("_veto_flags", ""))
            if veto: reason = f"🚫否决:{veto}"
            out.append(f"| {int(r['score_rank'])} | {code} | {name} | {r['total_score']:.1f} | {h1yy} | {q2qq} | {ind} | {reason} |")
        out.append("")

    # --- 🔴 Avoid Zone ---
    out.append(f"## 🔴 回避/做空信号 (评分<45或已过滤, {len(avoid)}只)")
    out.append("")
    if len(avoid) > 0:
        out.append("| 代码 | 简称 | 评分 | 风险信号 | 操作建议 |")
        out.append("|------|------|------|----------|----------|")
        for _, r in avoid.iterrows():
            risks = []
            if pd.notna(r.get("price_score")) and r["price_score"] < 35: risks.append("公告日暴跌")
            if pd.notna(r.get("precar_score")) and r["precar_score"] < 35: risks.append("PreCAR过热")
            if pd.isna(r.get("forecast_mid_yi")) or r.get("forecast_type") == "不确定": risks.append("预告不确定")
            if r.get("is_filtered"): risks.append(f"过滤:{r.get('filter_reason','')}")
            risk_s = ", ".join(risks) if risks else "评分偏低"
            out.append(f"| {r['code']} | {r['name']} | {r['total_score']:.1f} | {risk_s} | 回避 |")
        out.append("")

    # --- Data Quality Summary ---
    out.append("---")
    out.append("## 📊 数据质量")
    out.append("")
    data_per_row = (
        df["q1_profit_yi"].notna().astype(int)
        + df["open_gap_pct"].notna().astype(int)
        + df["mkt_cap_yi"].notna().astype(int)
        + df["pre_car_abnormal"].notna().astype(int)
    )
    avg_quality = data_per_row.mean()
    out.append(f"| 指标 | 值 |")
    out.append(f"|------|----|")
    out.append(f"| Q1 cjpy | {n_q1}/{len(df)} {'✅' if n_q1==len(df) else '⚠️'} |")
    out.append(f"| 行业分类(tushare) | {n_industry}/{len(df)} {'✅' if n_industry==len(df) else '⚠️'} |")
    out.append(f"| 市值(东财→代理) | {n_mkt}/{len(df)} {'✅' if n_mkt==len(df) else '⚠️代理失败，已用静态参考值填充'} |")
    out.append(f"| 价格事件 | {n_price}/{len(df)} {'✅' if n_price>len(df)//2 else '⚠️价格数据缺失，PreCAR/Price因子可能不准'} |")
    out.append(f"| 平均数据完整度 | {avg_quality:.1f}/4 |")
    out.append(f"| 统计方法 | Rank百分位(小样本稳健) |")
    out.append(f"| 数据源 | tushare(行业,预告) + cjpy(财报) + 东财(市值) |")
    out.append(f"| 模型版本 | v3.5 三源分离(减代理依赖) |")

    # --- Validation warnings (if any) ---
    if validation_result and validation_result.get("warning_fails"):
        out.append("")
        out.append(f"| 🔍 数据校验警告 | {validation_result['summary']} |")
        for wf in validation_result["warning_fails"]:
            out.append(f"|   ⚠️ {wf['name']} | {wf['detail']} |")

    out.append("")

    out.append("---")
    out.append("*⚠️ 量化系统自动生成，不构成投资建议。预告期(Phase1)数据置信度低，建议仅观察。*")

    return "\n".join(out)


def generate_full_table(df):
    """Generate the 17-column raw data table for morning push (kept for detail)."""
    lines = []
    today_str = TODAY.strftime("%Y-%m-%d")
    # Mark today's new
    today_new = df[df["notice_date"].astype(str).str.startswith(today_str)]

    lines.append(f"## 全量 {len(df)} 只沪深标的 · {today_str}")
    lines.append("")

    # Header
    header = "| 日期 | 代码 | 简称 | 类型 | Q1实 | 预告H1 | 去年H1 | H1YoY | Q1YoY | Q2隐含 | Q2YoY | Q2QoQ | SUE | Price | PreC | 涨跌 | 总分 |"
    sep    = "|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|"
    lines.append(header)
    lines.append(sep)

    today_codes = set(today_new["code"].tolist()) if len(today_new) > 0 else set()

    def sfy(v): return f"{float(v):+.1f}%" if pd.notna(v) and not (isinstance(v, float) and np.isnan(v)) else "--"
    def sf(v, f='.2f'): return format(float(v), f) if pd.notna(v) and not (isinstance(v, float) and np.isnan(v)) else "--"

    for _, r in df.sort_values("notice_date", ascending=False).iterrows():
        code = str(r["code"]).zfill(6)
        name = str(r["name"])[:8]
        date = str(r["notice_date"])[5:10]
        is_new = "🆕" if code in today_codes else ""
        day_ret = f"{float(r.get('day_return_pct')):+.1f}%" if pd.notna(r.get("day_return_pct")) else "--"
        lines.append(
            f"| {date} | {code} | {name} | {str(r.get('forecast_type',''))[:4]} | {sf(r['q1_profit_yi'])} | {sf(r['forecast_mid_yi'],'.1f')} | {sf(r['h1_prior_profit_yi'])} | {sfy(r.get('h1_prof_forecast_yoy_pct'))} | {sfy(r.get('q1_prof_yoy_pct'))} | {sf(r.get('q2_profit_yi_implied'))} | {sfy(r.get('q2_prof_implied_yoy_pct'))} | {sfy(r.get('q2_prof_implied_qoq_pct'))} | {sf(r.get('sue_score'),'.1f')} | {sf(r.get('price_score'),'.1f')} | {sf(r.get('precar_score'),'.1f')} | {day_ret} | {sf(r['total_score'],'.1f')} {is_new} |"
        )
    return "\n".join(lines)


def should_send_report(df):
    """Send-gate: only push when useful signals exist or new data arrived.
    Returns (should_send, reason_string)."""
    # Gate 1: Score spread — if everything clustered, no signal
    score_spread = df["total_score"].max() - df["total_score"].min()
    if score_spread < 10:
        return False, "Score区间过窄(Δ<10)，无区分度"

    # Gate 2: Strong signal check
    has_strong = ((df["total_score"] >= 55) | (df["total_score"] < 42)).sum()
    if has_strong == 0:
        return False, "全部标的处于中性区间，无强信号"

    # Gate 3: Data confidence
    data_per_row = (
        df["q1_profit_yi"].notna().astype(int)
        + df["open_gap_pct"].notna().astype(int)
        + df["mkt_cap_yi"].notna().astype(int)
    )
    avg_quality = data_per_row.mean()
    if avg_quality < 1.0:
        return False, "数据完整度过低"

    return True, f"正常(Δ={score_spread:.1f}, 强信号={has_strong})"



def save_csv(df, path=None):
    """Save results to CSV."""
    if path is None: path = OUT_CSV
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    csv_cols = [
        "code","name","source","notice_date","forecast_type",
        "forecast_lower_yi","forecast_upper_yi","forecast_mid_yi",
        "h1_prior_profit_yi",
        "q1_profit_yi","q1_revenue_yi","h1_profit_yi_actual","q2_profit_yi",
        "q1_profit_yi_prior","q2_profit_yi_prior",
        "q2_profit_yi_implied","q2_revenue_yi_implied",
        "h1_prof_forecast_yoy_pct",
        "q1_prof_yoy_pct","q1_rev_yoy_pct",
        "q2_prof_yoy_pct","q2_rev_yoy_pct",
        "q2_prof_implied_yoy_pct","q2_prof_implied_qoq_pct",
        "q2_rev_implied_yoy_pct","q2_rev_implied_qoq_pct",
        "sue_raw","sue_std","sue_method","sue_confidence","q2_expected_yi","seasonal_ratio","sue_score",
        "eq_accrual","eq_rev_growth","eq_profitability","eq_raw","eq_score",
        "open_gap_pct","intraday_pct","day_return_pct","vol_ratio","price_raw","price_score",
        "pre_car_raw","pre_car_abnormal","precar_score",
        "industry","industry_source","industry_momentum_pct","industry_score",
        "timing_delta_days","timing_score",
        "vol_anomaly_z","vol_anomaly_label","vol_bonus",
        "car_3d","car_5d","car_10d","car_20d","car_5d_abnormal",
        "mkt_cap_yi","mkt_cap_tier","concepts",
        "total_score","score_rank","is_filtered","filter_reason",
        "_coverage_ratio","_coverage_penalty","_interaction_bonus","_veto_flags",
        "rank_momentum","_rank_delta",
    ]
    available = [c for c in csv_cols if c in df.columns]
    df[available].to_csv(path, index=False, encoding="utf-8-sig")
    eprint(f"     CSV saved: {path} ({len(df)} rows)")
    return path

def save_report(report_text, path=None):
    if path is None: path = OUT_REPORT
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(report_text)
    eprint(f"     Report saved: {path} ({len(report_text)} chars)")
    return path

# ============================================================
# Phase 7: Backtesting (2025 H1 data)
# ============================================================

def run_backtest():
    """Run the full pipeline on 2025 H1 data and validate factor ICs."""
    eprint("=" * 60)
    eprint("  BACKTEST MODE: 2025 H1 data")
    eprint("=" * 60)

    # Step 1: Fetch forecasts
    fc_df = fetch_forecasts(period="20250630")
    ex_df = fetch_express_reports(period="20250630")

    # Step 2: Merge
    eprint("     Merging...")
    if not fc_df.empty and not ex_df.empty:
        all_codes_set = set(fc_df["code"].tolist() + ex_df["code"].tolist())
        merged = []
        for code in all_codes_set:
            ex_r = ex_df[ex_df["code"] == code]
            fc_r = fc_df[fc_df["code"] == code]
            if not ex_r.empty:
                row = ex_r.iloc[0].to_dict()
                if not fc_r.empty:
                    fc = fc_r.iloc[0]
                    for k in ["forecast_type","forecast_lower_yi","forecast_upper_yi","forecast_mid_yi","h1_forecast_yoy","h1_prior_profit_yi","change_text"]:
                        row[k] = fc.get(k, np.nan if "yi" in k or "yoy" in k else "")
                merged.append(row)
            elif not fc_r.empty:
                merged.append(fc_r.iloc[0].to_dict())
        df = pd.DataFrame(merged)
    elif not fc_df.empty:
        df = fc_df
    elif not ex_df.empty:
        df = ex_df
    else:
        eprint("  No 2025 data!"); return None

    eprint(f"  Merged: {len(df)} stocks")

    # --- Exclude 北交所 ---
    df = df[~df["code"].apply(is_bj_stock)]
    eprint(f"     Excluded BJ -> {len(df)} stocks remaining")

    all_codes = df["code"].unique().tolist()

    # For backtest: limit to stocks with valid data and top priority ones
    # Prioritize: 快报 > high forecast growth > rest
    df["_priority"] = 0
    df.loc[df["source"] == "快报", "_priority"] = 3
    df.loc[(df["h1_forecast_yoy"].abs() > 50) & (df["source"] != "快报"), "_priority"] = 2
    df.loc[df["forecast_type"].isin(["预增","扭亏","大增"]), "_priority"] = df["_priority"].clip(lower=1)
    df = df.sort_values("_priority", ascending=False)
    # Also filter to stocks with market cap info (will be enriched later)
    # Cap at ~200 stocks for backtest speed
    df = df.head(200).reset_index(drop=True)
    if "_priority" in df.columns: del df["_priority"]
    eprint(f"  Backtest scope: {len(df)} stocks (capped for speed)")
    all_codes = df["code"].unique().tolist()

    # Step 3: Income statements (2025 H1)
    fin_data = fetch_income_statements(all_codes, BT_PERIOD_H1, BT_PERIOD_Q1, BT_PERIOD_H1_PRIOR, BT_PERIOD_Q1_PRIOR)
    hist_q = fetch_historical_quarterly_profits(all_codes, BT_PERIOD_H1)

    # Step 4: Q2 + SUE
    df = calculate_q2_and_sue(df, fin_data, hist_q)

    # Step 5: Eastmoney enrichment (returns df, health dict)
    df, _ = enrich_eastmoney(df)

    # Step 6: Price data (start earlier for pre-CAR)
    earliest_notice = df["notice_date"].min()
    if pd.notna(earliest_notice):
        price_start = (earliest_notice - timedelta(days=60)).strftime("%Y%m%d")
    else:
        price_start = "20250401"
    # End date: use late enough to cover post-event drift
    price_end = "20260331"

    price_cache = fetch_price_data(all_codes, start_date=price_start, end_date=price_end)
    df = compute_all_event_metrics(df, price_cache)

    # Step 7: Scoring
    df = compute_scores(df)
    df = apply_filters(df)

    # Step 8: Compute forward returns for IC validation
    eprint("     Computing forward returns for IC...")
    for col in ["fwd_car_5d","fwd_car_10d","fwd_car_20d","fwd_car_5d_abn","fwd_car_10d_abn","fwd_car_20d_abn"]:
        df[col] = np.nan

    bm_df = price_cache.get("__benchmark__")

    _dbg_total, _dbg_has_pdf, _dbg_has_t0 = 0, 0, 0
    for idx, row in df.iterrows():

        code, notice = row["code"], row.get("notice_date")
        if pd.isna(notice): continue
        pdf = price_cache.get(code)
        if pdf is None or pdf.empty: continue
        t0_date, t0_idx = find_event_trading_day(notice, pdf)
        if t0_idx is None or t0_idx < 1: continue
        if _dbg_has_t0 <= 3:
            eprint(f"     DEBUG stock {code}: t0_idx={t0_idx}, len(pdf)={len(pdf)}")

        def _cum_fwd(start_idx, n):
            end_idx = min(start_idx + n, len(pdf) - 1)
            if end_idx <= start_idx: return np.nan
            sc = pdf.at[start_idx, "close"]
            ec = pdf.at[end_idx, "close"]
            return (ec - sc) / sc * 100 if sc and sc != 0 else np.nan

        for nd, col in [(5,"fwd_car_5d"),(10,"fwd_car_10d"),(20,"fwd_car_20d")]:
            df.at[idx, col] = _cum_fwd(t0_idx, nd)

        # Abnormal vs benchmark
        if bm_df is not None and not bm_df.empty:
            _, bm_t0 = find_event_trading_day(notice, bm_df)
            if bm_t0 is not None:
                def _bm_fwd(start_idx, n):
                    end_idx = min(start_idx + n, len(bm_df) - 1)
                    if end_idx <= start_idx: return np.nan
                    sc = bm_df.at[start_idx, "close"]
                    ec = bm_df.at[end_idx, "close"]
                    return (ec - sc) / sc * 100 if sc and sc != 0 else np.nan
                for nd, col in [(5,"fwd_car_5d_abn"),(10,"fwd_car_10d_abn"),(20,"fwd_car_20d_abn")]:
                    bm_ret = _bm_fwd(bm_t0, nd)
                    fwd_ret = df.at[idx, f"fwd_car_{nd}d"]
                    if not np.isnan(fwd_ret) and not np.isnan(bm_ret):
                        df.at[idx, col] = fwd_ret - bm_ret

    has_bm = bm_df is not None and not bm_df.empty

    # Fallback: if abnormal returns all NaN, copy raw returns as proxy
    for horizon in ["5d","10d","20d"]:
        raw_col = f"fwd_car_{horizon}"
        abn_col = f"fwd_car_{horizon}_abn"
        if df[abn_col].notna().sum() == 0 and df[raw_col].notna().sum() > 0:
            eprint(f"     WARNING: {abn_col} all NaN, using {raw_col} as proxy")
            df[abn_col] = df[raw_col]

    # Step 9: Compute ICs
    eprint("")
    eprint("=" * 60)
    eprint("  BACKTEST RESULTS")
    eprint("=" * 60)

    scores = df["total_score"]
    factors = {
        "SUE (Z)": df["sue_z"],
        "Price Confirm (Z)": df["price_z"],
        "Pre-CAR Inverse (Z)": df["pre_car_z"],
        "Industry Resonance (Z)": df["industry_resonance_z"],
        "Composite (Z)": df["total_z"],
    }

    # Rank IC
    for horizon, fcol in [("5d","fwd_car_5d_abn"),("10d","fwd_car_10d_abn"),("20d","fwd_car_20d_abn")]:
        fwd = df[fcol].dropna()
        eprint(f"\n  --- Forward {horizon} Abnormal Return (n={len(fwd)}) ---")
        for flabel, fseries in factors.items():
            ic = spearman_ic(fseries, df[fcol])
            if not np.isnan(ic):
                eprint(f"    Rank IC({flabel:25s}): {ic:+.4f}")

    # Factor correlation matrix
    factor_cols = ["sue_z","price_z","pre_car_z","industry_resonance_z"]
    avail_cols = [c for c in factor_cols if c in df.columns]
    if len(avail_cols) >= 2:
        corr = df[avail_cols].corr()
        eprint(f"\n  Factor Correlation Matrix:")
        eprint(corr.to_string())

        # Effective N (from eigenvalues)
        eigenvalues = np.linalg.eigvalsh(corr.values)
        eff_n = sum(eigenvalues)**2 / sum(e**2 for e in eigenvalues)
        eprint(f"  Effective N (from eigenvalues): {eff_n:.1f} / {len(avail_cols)}")

    # Top 5 by score with forward returns
    eprint(f"\n  Top 5 by v2.0 score:")
    top5 = df.nlargest(5, "total_score")
    for _, r in top5.iterrows():
        f5 = r.get("fwd_car_5d_abn") if not pd.isna(r.get("fwd_car_5d_abn")) else r.get("fwd_car_5d")
        f5s = f"{f5:+.1f}%" if not pd.isna(f5) else "N/A"
        f20 = r.get("fwd_car_20d_abn") if not pd.isna(r.get("fwd_car_20d_abn")) else r.get("fwd_car_20d")
        f20s = f"{f20:+.1f}%" if not pd.isna(f20) else "N/A"
        eprint(f"    {r['code']} {r['name']}: score={r['total_score']:.1f}  fwd_5d={f5s}  fwd_20d={f20s}")

    # Save backtest JSON
    bt_results = {
        "model": "v2.0 4-factor Z-Score",
        "backtest_period": "2025 H1",
        "n_stocks": len(df),
        "n_with_price": int(df["open_gap_pct"].notna().sum()),
        "weights": WEIGHTS,
        "rank_ic": {},
        "factor_correlations": corr.round(3).to_dict() if len(avail_cols) >= 2 else {},
        "effective_n": eff_n if len(avail_cols) >= 2 else None,
    }

    for horizon, fcol in [("5d","fwd_car_5d_abn"),("10d","fwd_car_10d_abn"),("20d","fwd_car_20d_abn")]:
        ic_entry = {}
        for flabel, fseries in factors.items():
            ic = spearman_ic(fseries, df[fcol])
            if not np.isnan(ic):
                ic_entry[flabel] = round(ic, 4)
        if ic_entry:
            bt_results["rank_ic"][horizon] = ic_entry

        valid = df[fcol].notna()
        if valid.sum() >= 10:
            q = pd.qcut(df.loc[valid, "total_score"], min(5, valid.sum()), labels=False, duplicates="drop")
            if q.nunique() >= 2:
                top_q = df.loc[valid][q == q.max()]
                bot_q = df.loc[valid][q == q.min()]
                bt_results.setdefault("long_short", {})[horizon] = {
                    "top_avg": round(float(top_q[fcol].mean()), 2),
                    "bot_avg": round(float(bot_q[fcol].mean()), 2),
                    "spread": round(float(top_q[fcol].mean() - bot_q[fcol].mean()), 2),
                    "hit_rate": round(float((top_q[fcol] > 0).sum() / len(top_q)), 3),
                }

    bt_path = BT_OUT
    bt_path.parent.mkdir(parents=True, exist_ok=True)
    with open(bt_path, "w", encoding="utf-8") as f:
        json.dump(bt_results, f, ensure_ascii=False, indent=2, default=str)
    eprint(f"\n  Backtest JSON saved: {bt_path}")

    # Also save CSV and report
    save_csv(df)
    report = generate_report(df)
    save_report(report)

    return df

# ============================================================
# Main Orchestrator
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="半年报投资决策系统 v2.0")
    parser.add_argument("--backtest", action="store_true", dest="backtest",
                        help="Run on 2025 H1 data with IC validation")
    parser.add_argument("--no-backtest", action="store_false", dest="backtest",
                        help="Forward-looking run on 2026 H1 (default)")
    parser.set_defaults(backtest=False)
    parser.add_argument("--out", type=str, default=str(OUT_CSV))
    parser.add_argument("--report", type=str, default=str(OUT_REPORT))
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--codes", type=str, default=None, help="Comma-separated stock codes")
    parser.add_argument("--no-price", action="store_true", help="Skip price data")
    parser.add_argument("--no-eastmoney", action="store_true")
    args = parser.parse_args()

    # --backtest mode
    if args.backtest:
        run_backtest()
        return

    out_csv = Path(args.out)
    out_report = Path(args.report)

    eprint("=" * 60)
    eprint(f"  半年报投资决策系统 v3.3  {TODAY.strftime('%Y-%m-%d %H:%M')}")
    eprint(f"  6-Factor Rank + Continuous Interaction + Bayesian Shrinkage")
    eprint("=" * 60)

    # Step 1-2: Fetch data
    fc_df = fetch_forecasts()
    ex_df = fetch_express_reports()

    # Step 3: Merge
    eprint("     Merging...")
    if not fc_df.empty and not ex_df.empty:
        all_codes_set = set(fc_df["code"].tolist() + ex_df["code"].tolist())
        merged = []
        for code in all_codes_set:
            ex_r = ex_df[ex_df["code"] == code]
            fc_r = fc_df[fc_df["code"] == code]
            if not ex_r.empty:
                row = ex_r.iloc[0].to_dict()
                if not fc_r.empty:
                    fc = fc_r.iloc[0]
                    for k in ["forecast_type","forecast_lower_yi","forecast_upper_yi","forecast_mid_yi","h1_forecast_yoy","h1_prior_profit_yi","change_text"]:
                        row[k] = fc.get(k, np.nan if "yi" in k or "yoy" in k else "")
                merged.append(row)
            elif not fc_r.empty:
                merged.append(fc_r.iloc[0].to_dict())
        df = pd.DataFrame(merged)
    elif not fc_df.empty:
        df = fc_df
    elif not ex_df.empty:
        df = ex_df
    else:
        eprint("  ERROR: No data!"); sys.exit(1)

    eprint(f"  Merged: {len(df)} stocks")

    # --- Exclude 北交所 ---
    n_before = len(df)
    df = df[~df["code"].apply(is_bj_stock)]
    eprint(f"     Excluded BJ: {n_before - len(df)} stocks ({len(df)} remaining)")
    df = df.reset_index(drop=True)

    if args.codes:
        target = set(c.strip() for c in args.codes.split(","))
        df = df[df["code"].isin(target)]
        eprint(f"  Filtered: {len(df)} stocks (--codes)")

    all_codes = df["code"].unique().tolist()

    # Step 4: Income statements
    fin_data = fetch_income_statements(all_codes, PERIOD_H1, PERIOD_Q1, PERIOD_H1_PRIOR, PERIOD_Q1_PRIOR)
    hist_q = fetch_historical_quarterly_profits(all_codes, PERIOD_H1)

    # Step 5: Q2 + SUE
    df = calculate_q2_and_sue(df, fin_data, hist_q)

    # Initialize data source health tracker
    data_health = {
        "industry": {"status": "skipped", "matched": 0, "total": len(df)},
        "mkt_cap": {"status": "skipped", "live": 0, "total": len(df), "fallback": 0, "failed": 0},
        "price": {"status": "ok", "stocks": 0, "total": len(all_codes)},
        "q1_fmdata": {"status": "ok", "stocks": len(fin_data), "total": len(all_codes)},
        "forecasts": {"status": "ok", "source": "tushare", "count": len(df)},
    }

    # Step 6a: Industry from tushare (no proxy needed)
    if not args.no_eastmoney:
        df, industry_health = enrich_industry_from_tushare(df)
        data_health["industry"] = industry_health
    else:
        if "industry" not in df.columns:
            df["industry"] = ""

    # Step 6b: Market cap from Eastmoney (needs proxy, but only 1 field)
    if not args.no_eastmoney:
        df, mkt_health = enrich_market_cap_from_eastmoney(df)
        data_health["mkt_cap"] = mkt_health
    else:
        for c in ["mkt_cap_yi", "mkt_cap_tier"]:
            if c not in df.columns: df[c] = np.nan
        df["mkt_cap_tier"] = df["mkt_cap_tier"].fillna("N/A")

    # Step 7: Price data + event study
    if not args.no_price and len(df) > 0:
        earliest = df["notice_date"].min()
        if pd.notna(earliest):
            price_start = (earliest - timedelta(days=40)).strftime("%Y%m%d")
        else:
            price_start = "20260401"
        price_cache = fetch_price_data(all_codes, start_date=price_start, end_date=TODAY_STR)
        data_health["price"]["stocks"] = len(price_cache) - 1  # exclude __benchmark__
        if data_health["price"]["stocks"] < data_health["price"]["total"] * 0.5:
            data_health["price"]["status"] = "error"
            eprint(f"     🔴 Price data: {data_health['price']['stocks']}/{data_health['price']['total']} stocks (preCAR/Price factors degraded)")
        elif data_health["price"]["stocks"] < data_health["price"]["total"]:
            data_health["price"]["status"] = "warn"
            eprint(f"     🟡 Price data: {data_health['price']['stocks']}/{data_health['price']['total']} stocks (some CARs NaN)")
        df = compute_all_event_metrics(df, price_cache)
    else:
        for c in ["open_gap_pct","intraday_pct","day_return_pct","vol_ratio",
                   "car_3d","car_5d","car_10d","car_20d","car_5d_abnormal",
                   "pre_car_raw","pre_car_abnormal","event_days_available"]:
            df[c] = np.nan
        data_health["price"]["stocks"] = 0
        data_health["price"]["status"] = "skipped"

    # ---- Data Validation Gate (before scoring) ----
    validation = validate_data(df, data_health)
    if validation["block"]:
        eprint("=" * 60)
        eprint("  🔴 ABORT: Data validation failed. No report generated.")
        eprint(f"  {validation['summary']}")
        for cf in validation["critical_fails"]:
            eprint(f"    [{cf['name']}] {cf['detail']}")
        eprint("=" * 60)
        # Still print DATA_HEALTH for diagnostics
        print(f"# DATA_HEALTH: industry={data_health['industry']['status']}({data_health['industry']['matched']}/{data_health['industry']['total']}) mkt_cap={data_health['mkt_cap']['status']}({data_health['mkt_cap']['live']}L/{data_health['mkt_cap']['fallback']}F/{data_health['mkt_cap']['total']}T) price={data_health['price']['status']}({data_health['price']['stocks']}/{data_health['price']['total']}) q1={data_health['q1_fmdata']['stocks']}/{data_health['q1_fmdata']['total']} forecast={data_health['forecasts']['source']}({data_health['forecasts']['count']})")
        print(validation["summary_line"])
        sys.exit(1)

    # Step 8: Scoring + Filters
    df = compute_scores(df)
    df = apply_filters(df)

    # Step 9: Rank momentum (v3.4)
    df = compute_rank_momentum(df)
    save_rank_history(df)

    # Step 10: Walk-Forward validation (daily pseudo-IC)
    daily_ic = compute_daily_pseudo_ic(df)

    # Step 11: Output
    phase = detect_earnings_phase(df)
    save_csv(df, out_csv)

    # Send gate check
    should_send, gate_reason = should_send_report(df)

    if not args.no_report:
        report = generate_report(df, phase=phase, validation_result=validation)
        save_report(report, out_report)

        # Generate full table for cron push
        full_table = generate_full_table(df)

        if not should_send:
            eprint(f"  SEND GATE: blocked ({gate_reason})")
        else:
            eprint(f"  SEND GATE: allowed ({gate_reason})")

        # ---- Data Source Health (always print) ----
        print(validation["summary_line"])
        print(f"# DATA_HEALTH: industry={data_health['industry']['status']}({data_health['industry']['matched']}/{data_health['industry']['total']}) mkt_cap={data_health['mkt_cap']['status']}({data_health['mkt_cap']['live']}L/{data_health['mkt_cap']['fallback']}F/{data_health['mkt_cap']['total']}T) price={data_health['price']['status']}({data_health['price']['stocks']}/{data_health['price']['total']}) q1={data_health['q1_fmdata']['stocks']}/{data_health['q1_fmdata']['total']} forecast={data_health['forecasts']['source']}({data_health['forecasts']['count']})")
        print(f"# GATE: {gate_reason}")
        print("")
        print(report[:800])
        if len(report) > 800:
            print(f"\n... (full report: {out_report})")

        # Print validation if data available
        if daily_ic:
            v_lines = [f"\n## 📊 日频验证 (v3.4 Walk-Forward Tier 1)"]
            v_lines.append(f"IC: {daily_ic['daily_ic']:.4f} ({daily_ic['n_obs']} observations)")
            v_lines.append(f"T-stat: {daily_ic['t_stat']:.2f} (p={daily_ic['p_value']:.4f})")
            v_lines.append(f"Top5 avg return: {daily_ic['top5_avg']:.1f}% | Bot5: {daily_ic['bot5_avg']:.1f}%")
            v_lines.append(f"Hit rate: {daily_ic['hit_rate']:.1%} | {'✅显著' if daily_ic['significant'] else '⚠不显著'}")
            print("\n".join(v_lines))
    else:
        top5 = df.nlargest(5, "total_score")
        print("Top 5:")
        for _, r in top5.iterrows():
            print(f"  {r['code']} {r['name']}: {r['total_score']:.1f} (SUE={r.get('sue_score',0):.0f} EQ={r.get('eq_score',0):.0f} Price={r.get('price_score',0):.0f} PreCAR={r.get('precar_score',0):.0f})")

    eprint("=" * 60)
    eprint("  Done.")
    eprint(f"  CSV: {out_csv}")
    if not args.no_report:
        eprint(f"  Report: {out_report}")
    eprint(f"  Gate: {gate_reason}")
    eprint("=" * 60)

if __name__ == "__main__":
    main()

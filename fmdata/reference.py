"""Reference data layer — base dictionaries cached in memory."""
import logging
from functools import lru_cache

import pandas as pd

from fmdata.config import REFERENCE_DIR
from fmdata.fetcher import TushareFetcher

logger = logging.getLogger("fmdata.reference")

# Module-level cache
_cache = {}


def _get_ts():
    return TushareFetcher()


def _ensure_dir():
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


# ---- Trade Calendar ----

def trade_calendar() -> pd.DataFrame:
    """Full trade calendar DataFrame: cal_date, is_open, pretrade_date."""
    if "trade_calendar" in _cache:
        return _cache["trade_calendar"]

    path = REFERENCE_DIR / "trade_calendar.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = _fetch_trade_calendar()

    # Ensure sorted by date
    df = df.sort_values("cal_date").reset_index(drop=True)
    _cache["trade_calendar"] = df
    return df


def _fetch_trade_calendar() -> pd.DataFrame:
    ts = _get_ts()
    df = ts.trade_cal(exchange="SSE", start_date="20100101", end_date="20291231")
    _ensure_dir()
    path = REFERENCE_DIR / "trade_calendar.csv"
    df.to_csv(path, index=False)
    logger.info(f"trade_calendar fetched: {len(df)} rows -> {path}")
    return df


def _cal_as_str(cal):
    """Ensure cal_date column is string for consistent comparison."""
    df = cal.copy()
    df["cal_date"] = df["cal_date"].astype(str)
    return df


def is_trade_day(date: str) -> bool:
    """Check if a date is a trading day. date format: YYYYMMDD or YYYY-MM-DD."""
    date = date.replace("-", "")
    cal = _cal_as_str(trade_calendar())
    row = cal[cal["cal_date"] == date]
    if row.empty:
        return False
    return bool(row.iloc[0]["is_open"])


def last_trade_day() -> str:
    """Return the most recent trading day as YYYYMMDD string."""
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    cal = _cal_as_str(trade_calendar())
    trading_days = cal[(cal["is_open"] == 1) & (cal["cal_date"] <= today)]
    if trading_days.empty:
        return None
    return str(trading_days.iloc[-1]["cal_date"])


def next_trade_day(date: str) -> str:
    """Return the next trading day after the given date."""
    date = date.replace("-", "")
    cal = _cal_as_str(trade_calendar())
    future = cal[(cal["is_open"] == 1) & (cal["cal_date"] > date)]
    if future.empty:
        return None
    return str(future.iloc[0]["cal_date"])


def prev_trade_day(date: str) -> str:
    """Return the previous trading day before the given date."""
    date = date.replace("-", "")
    cal = _cal_as_str(trade_calendar())
    past = cal[(cal["is_open"] == 1) & (cal["cal_date"] < date)]
    if past.empty:
        return None
    return str(past.iloc[-1]["cal_date"])


# ---- Stock List ----

def stock_list() -> pd.DataFrame:
    """Full A-share stock list: ts_code, name, industry, list_date, status."""
    if "stock_list" in _cache:
        return _cache["stock_list"]

    path = REFERENCE_DIR / "stock_list.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = _fetch_stock_list()

    _cache["stock_list"] = df
    return df


def _fetch_stock_list() -> pd.DataFrame:
    ts = _get_ts()
    df = ts.stock_basic(exchange="", list_status="L",
                        fields="ts_code,symbol,name,area,industry,market,list_date")
    _ensure_dir()
    path = REFERENCE_DIR / "stock_list.csv"
    df.to_csv(path, index=False)
    logger.info(f"stock_list fetched: {len(df)} rows -> {path}")
    return df


# ---- Tech Stock List ----

# Shenwan L3 industries classified as "tech" (tushare stock_basic industry field)
_TECH_INDUSTRIES = [
    "半导体", "元器件", "软件服务", "通信设备", "IT设备", "互联网",
    "电器仪表", "电气设备", "专用机械", "机械基件", "化工原料",
    "汽车配件", "汽车整车", "汽车服务", "小金属", "铝", "铜",
    "航空", "医疗保健", "生物制药", "化学制药", "中成药",
    "机床制造", "工程机械", "运输设备", "环境保护",
    "矿物制品", "塑料", "化纤",
]


def tech_stock_list() -> pd.DataFrame:
    """Tech stock subset derived from stock_list."""
    if "tech_stock_list" in _cache:
        return _cache["tech_stock_list"]

    path = REFERENCE_DIR / "tech_stock_list.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        all_stocks = stock_list()
        df = all_stocks[all_stocks["industry"].isin(_TECH_INDUSTRIES)]
        _ensure_dir()
        df.to_csv(path, index=False)
        logger.info(f"tech_stock_list derived: {len(df)} rows -> {path}")

    _cache["tech_stock_list"] = df
    return df


# ---- SW Industry List ----

def industry_list() -> pd.DataFrame:
    """Shenwan L1 industry list: industry_code, industry_name."""
    if "industry_list" in _cache:
        return _cache["industry_list"]

    path = REFERENCE_DIR / "sw_industry_list.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = _fetch_industry_list()

    _cache["industry_list"] = df
    return df


def _fetch_industry_list() -> pd.DataFrame:
    ts = _get_ts()
    df = ts.index_classify(level="L1", src="SW2021")
    _ensure_dir()
    path = REFERENCE_DIR / "sw_industry_list.csv"
    df.to_csv(path, index=False)
    logger.info(f"industry_list fetched: {len(df)} rows -> {path}")
    return df


# ---- Stock-Industry Mapping ----

def stock_industry_map() -> pd.DataFrame:
    """Stock-to-industry mapping: ts_code, industry_code, industry_name."""
    if "stock_industry_map" in _cache:
        return _cache["stock_industry_map"]

    path = REFERENCE_DIR / "stock_industry_map.csv"
    if path.exists():
        df = pd.read_csv(path)
    else:
        df = _build_stock_industry_map()

    _cache["stock_industry_map"] = df
    return df


def _build_stock_industry_map() -> pd.DataFrame:
    ts = _get_ts()
    industries = industry_list()
    rows = []
    for _, ind in industries.iterrows():
        code = ind.get("index_code", ind.get("industry_code", ""))
        if not code:
            continue
        try:
            members = ts.index_member(index_code=code)
            if members is not None and not members.empty:
                col_con = members.columns[0]  # usually con_code or ts_code
                for _, m in members.iterrows():
                    ts_code = m.get("con_code", m.get("ts_code", ""))
                    if ts_code:
                        rows.append({
                            "ts_code": ts_code,
                            "industry_code": code,
                            "industry_name": ind.get("industry_name", ""),
                        })
        except Exception as e:
            logger.warning(f"failed to get members for {code}: {e}")
            continue

    df = pd.DataFrame(rows)
    _ensure_dir()
    path = REFERENCE_DIR / "stock_industry_map.csv"
    df.to_csv(path, index=False)
    logger.info(f"stock_industry_map built: {len(df)} rows -> {path}")
    return df


# ---- Query helpers ----

def get_industry(ts_code: str) -> str:
    """Get industry name for a stock."""
    mapping = stock_industry_map()
    row = mapping[mapping["ts_code"] == ts_code]
    if row.empty:
        return ""
    return str(row.iloc[0]["industry_name"])


def get_industry_stocks(industry_code: str) -> list:
    """Get all stock codes in an industry."""
    mapping = stock_industry_map()
    rows = mapping[mapping["industry_code"] == industry_code]
    return rows["ts_code"].tolist()


# ---- Refresh ----

def update_reference(name: str = None):
    """Force refresh reference data from API."""
    if name == "trade_calendar" or name is None:
        _cache.pop("trade_calendar", None)
        _fetch_trade_calendar()
    if name == "stock_list" or name is None:
        _cache.pop("stock_list", None)
        _cache.pop("tech_stock_list", None)
        _fetch_stock_list()
        # Rebuild tech list
        all_stocks = stock_list()
        tech = all_stocks[all_stocks["industry"].isin(_TECH_INDUSTRIES)]
        path = REFERENCE_DIR / "tech_stock_list.csv"
        tech.to_csv(path, index=False)
        _cache["tech_stock_list"] = tech
    if name == "industry_list" or name is None:
        _cache.pop("industry_list", None)
        _fetch_industry_list()
    if name == "stock_industry_map" or name is None:
        _cache.pop("stock_industry_map", None)
        _build_stock_industry_map()
    logger.info(f"reference updated: {name or 'all'}")

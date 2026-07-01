"""Market data layer — daily prices, industry indices, fundamentals."""
import logging
from pathlib import Path

import pandas as pd

from fmdata.config import MARKET_DIR, FUNDAMENTALS_DIR, FACTORS_DIR, STORE_DIR
from fmdata.registry import get_dataset, update_dataset_stats

logger = logging.getLogger("fmdata.market")


def _load_csv(name: str, date_col=None) -> pd.DataFrame:
    """Load a CSV from store, return empty DataFrame if missing."""
    ds = get_dataset(name)
    if not ds:
        logger.warning(f"dataset {name} not in registry")
        return pd.DataFrame()
    path = STORE_DIR / ds["file"]
    if not path.exists():
        logger.warning(f"file not found: {path}")
        return pd.DataFrame()
    df = pd.read_csv(path)
    if date_col and date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col])
    return df


def _resolve_path(rel_path: str) -> Path:
    return STORE_DIR / rel_path


# ---- Daily Price Matrix ----

def daily_matrix() -> pd.DataFrame:
    """Tech stock daily price matrix: ts_code × dates."""
    ds = get_dataset("daily_matrix")
    if not ds:
        return pd.DataFrame()
    path = _resolve_path(ds["file"])
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


# ---- SW Industry Data ----

def sw_industry_close() -> pd.DataFrame:
    """Shenwan L1 industry daily close prices."""
    return _load_csv("sw_first_level_close", "date")


def sw_industry_amount() -> pd.DataFrame:
    """Shenwan L1 industry daily trading amounts."""
    return _load_csv("sw_first_level_amount", "date")


def sw_pe_history() -> pd.DataFrame:
    """Shenwan L1 industry PE history."""
    return _load_csv("sw_pe_history", "date")


def sw_pb_history() -> pd.DataFrame:
    """Shenwan L1 industry PB history."""
    return _load_csv("sw_pb_history", "date")


def sw_main_flow_net() -> pd.DataFrame:
    """Shenwan industry main capital flow net."""
    return _load_csv("sw_main_flow_net")


def sw_main_flow_pct() -> pd.DataFrame:
    """Shenwan industry main capital flow pct."""
    return _load_csv("sw_main_flow_pct")


# ---- Benchmark ----

def hs300(freq="daily") -> pd.DataFrame:
    """HS300 index data. freq='daily' or 'monthly'."""
    name = f"hs300_{freq}"
    return _load_csv(name, "date")


# ---- Tech Stock Indicators ----

def tech_indicators() -> pd.DataFrame:
    """Tech stock daily technical indicators."""
    return _load_csv("tech_indicators")


def turnover_matrix() -> pd.DataFrame:
    """Tech stock turnover rate matrix."""
    return _load_csv("turnover_matrix")


# ---- North Money (stored in macro/) ----

def north_money() -> pd.DataFrame:
    """North-bound capital flow daily data."""
    return _load_csv("north_money", "trade_date")


# ---- Fundamentals ----

def stock_fina(period: str = None) -> pd.DataFrame:
    """Quarterly financial data for a specific period.

    Args:
        period: Report period in YYYYMMDD format (e.g. '20260331').
                If None, returns the latest available period.
    """
    ds = get_dataset("stock_fina")
    if not ds:
        return pd.DataFrame()
    fina_dir = _resolve_path("fundamentals/stock_fina")

    if period:
        path = fina_dir / f"fina_{period}.csv"
        if not path.exists():
            logger.warning(f"fina file not found: {path}")
            return pd.DataFrame()
        return pd.read_csv(path)

    # Return latest period
    files = sorted(fina_dir.glob("fina_*.csv"))
    if not files:
        return pd.DataFrame()
    return pd.read_csv(files[-1])


def stock_fina_extended() -> pd.DataFrame:
    """Extended financial data (subset of stocks with additional metrics)."""
    return _load_csv("stock_fina_extended")


# ---- Factors ----

def factor_matrix() -> pd.DataFrame:
    """Unified factor matrix for industry rotation."""
    return _load_csv("factor_matrix")


# ---- SW Members (JSON) ----

def sw_members() -> dict:
    """Shenwan industry members mapping (from JSON file)."""
    path = _resolve_path("market/sw_members.json")
    if not path.exists():
        # Try legacy location
        legacy = Path("/home/ubuntu/claude-workspace/data/sw_industry/sw_members.json")
        if legacy.exists():
            import json
            with open(legacy) as f:
                return json.load(f)
        return {}
    import json
    with open(path) as f:
        return json.load(f)


# ---- Data Status ----

def data_status(name: str = None) -> dict:
    """Get status of market datasets."""
    from fmdata.registry import list_datasets
    all_ds = list_datasets()
    if name:
        ds = all_ds.get(name)
        if not ds:
            return {}
        path = _resolve_path(ds["file"])
        return {
            "name": name,
            "file": str(path),
            "exists": path.exists(),
            "rows": ds.get("rows", 0),
            "last_updated": ds.get("last_updated"),
            "date_range": ds.get("date_range"),
        }
    return {
        k: {
            "category": v.get("category", ""),
            "rows": v.get("rows", 0),
            "last_updated": v.get("last_updated"),
            "date_range": v.get("date_range"),
        }
        for k, v in all_ds.items()
    }

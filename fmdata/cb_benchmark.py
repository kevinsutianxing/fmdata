"""Official CSI convertible-bond benchmark utilities using fractional returns.

``benchmark_return`` represents CSI 000832 and matches ``cb_return_1d`` units:
``0.0062`` means ``+0.62%``. Raw akshare/tushare percentage-point columns are
converted exactly once; already normalized ``benchmark_return`` stays unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from fmdata.config import MARKET_DIR

DEFAULT_INDEX_FILE: Final[Path] = MARKET_DIR / "csi_cb_idx_hist.csv"
OFFICIAL_SOURCE: Final[str] = "csi_000832"
_DATE_CANDIDATES = ("date", "日期", "trade_date")
_RETURN_CANDIDATES = ("benchmark_return", "涨跌幅", "pct_chg")
_PERCENT_RETURN_COLUMNS = {"涨跌幅", "pct_chg"}
_SYMBOL_CANDIDATES = ("symbol", "代码", "index_code")


def _first_present(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in frame.columns:
            return name
    raise ValueError(f"missing required column; expected one of {candidates}")


def _validate_official_history(frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValueError("official CSI 000832 history is empty")
    if frame["date"].isna().any() or frame["benchmark_return"].isna().any():
        raise ValueError("official CSI 000832 history contains invalid date/return rows")
    if frame["date"].duplicated().any():
        raise ValueError("official CSI 000832 history contains duplicate dates")
    if not frame["date"].is_monotonic_increasing:
        raise ValueError("official CSI 000832 dates are not monotonic")
    if (frame["benchmark_return"].abs() > 0.30).any():
        raise ValueError("official CSI 000832 fractional daily return exceeds 30%; check unit/source")
    if len(frame) < 20:
        raise ValueError("official CSI 000832 history has fewer than 20 observations")


def load_official_cb_index(path: str | Path | None = None) -> pd.DataFrame:
    source_path = Path(path) if path is not None else DEFAULT_INDEX_FILE
    if not source_path.exists():
        raise FileNotFoundError(
            f"official CSI 000832 file not found: {source_path}; "
            "run `fmdata fetch csi_cb_idx_hist` or pass --official-index-file"
        )
    frame = pd.read_csv(source_path)
    symbol_col = next((name for name in _SYMBOL_CANDIDATES if name in frame.columns), None)
    if symbol_col is not None:
        symbols = frame[symbol_col].dropna().astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6).unique()
        if len(symbols) and set(symbols) != {"000832"}:
            raise ValueError(f"official benchmark file contains unexpected symbols: {sorted(symbols)}")

    date_col = _first_present(frame, _DATE_CANDIDATES)
    return_col = _first_present(frame, _RETURN_CANDIDATES)
    out = frame[[date_col, return_col]].rename(columns={date_col: "date", return_col: "benchmark_return"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["benchmark_return"] = pd.to_numeric(out["benchmark_return"], errors="coerce")
    if return_col in _PERCENT_RETURN_COLUMNS:
        out["benchmark_return"] = out["benchmark_return"] / 100.0
    out = out.dropna(subset=["date", "benchmark_return"]).sort_values("date").reset_index(drop=True)
    _validate_official_history(out)
    out["benchmark_source"] = OFFICIAL_SOURCE
    out["benchmark_unit"] = "fraction"
    return out


def build_market_daily(
    cb_raw: pd.DataFrame | None = None,
    official_index_file: str | Path | None = None,
) -> pd.DataFrame:
    del cb_raw
    return load_official_cb_index(official_index_file)

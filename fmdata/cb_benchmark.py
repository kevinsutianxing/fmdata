"""Official CSI convertible-bond index benchmark utilities.

The strategy engine's ``benchmark_return`` column must represent CSI 000832,
not a locally constructed equal-weight convertible-bond universe.  This module
keeps the source injectable so panel builders can use either a pre-fetched
fmdata CSV or a caller-supplied file without hard-coded absolute paths.

Unit contract is FRACTION, matching ``cb_return_1d``: ``0.0062`` means
``+0.62%``. Raw akshare ``涨跌幅`` / tushare ``pct_chg`` inputs are percentage
points and are divided by 100 exactly once. Already-normalized
``benchmark_return`` inputs are assumed to be fractions.
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


def _first_present(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in frame.columns:
            return name
    raise ValueError(f"missing required column; expected one of {candidates}")


def load_official_cb_index(path: str | Path | None = None) -> pd.DataFrame:
    """Load official CSI 000832 daily fractional returns from CSV."""
    source_path = Path(path) if path is not None else DEFAULT_INDEX_FILE
    if not source_path.exists():
        raise FileNotFoundError(
            f"official CSI 000832 file not found: {source_path}; "
            "run `fmdata fetch csi_cb_idx_hist` or pass --official-index-file"
        )
    frame = pd.read_csv(source_path)
    date_col = _first_present(frame, _DATE_CANDIDATES)
    return_col = _first_present(frame, _RETURN_CANDIDATES)
    out = frame[[date_col, return_col]].rename(
        columns={date_col: "date", return_col: "benchmark_return"}
    )
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["benchmark_return"] = pd.to_numeric(out["benchmark_return"], errors="coerce")
    if return_col in _PERCENT_RETURN_COLUMNS:
        out["benchmark_return"] = out["benchmark_return"] / 100.0
    out = out.dropna(subset=["date", "benchmark_return"])
    out = out.sort_values("date").drop_duplicates("date", keep="last")
    out["benchmark_source"] = OFFICIAL_SOURCE
    out["benchmark_unit"] = "fraction"
    return out.reset_index(drop=True)


def build_market_daily(
    cb_raw: pd.DataFrame | None = None,
    official_index_file: str | Path | None = None,
) -> pd.DataFrame:
    """Return official CSI 000832 benchmark data for CB panel construction."""
    del cb_raw
    return load_official_cb_index(official_index_file)

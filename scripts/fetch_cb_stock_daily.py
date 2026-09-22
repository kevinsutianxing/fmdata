#!/usr/bin/env python3
"""Build daily close history for all convertible-bond underlyings via fmdata."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

FMDATA_ROOT = Path("/home/ubuntu/fmdata")
sys.path.insert(0, str(FMDATA_ROOT))
from fmdata.config import STORE_DIR  # noqa: E402
from fmdata.fetcher import TushareFetcher  # noqa: E402
from fmdata.registry import register_dataset, scan_csv  # noqa: E402


def _builder():
    path = FMDATA_ROOT / "scripts" / "build_cb_panel.py"
    spec = importlib.util.spec_from_file_location("cb_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(start: str, end: str) -> pd.DataFrame:
    builder = _builder()
    fetcher = TushareFetcher()
    api_start = pd.Timestamp(start).strftime("%Y%m%d")
    api_end = pd.Timestamp(end).strftime("%Y%m%d")
    basic = builder.fetch_cb_basic(fetcher)
    bonds = builder.window_bonds(basic, api_start, api_end)
    codes = set(bonds["stock_code"].dropna().astype(str).str.zfill(6))
    days = builder._trade_days(fetcher, api_start, api_end)
    data = builder.fetch_stock_panel(fetcher, codes, days)
    if data.empty:
        raise RuntimeError("no underlying-stock daily data returned")
    data = data[["stock_code", "date", "stock_close", "stock_pct_chg"]].copy()
    data["source"] = "tushare_daily"
    data["source_is_pit"] = True
    return data.sort_values(["stock_code", "date"]).drop_duplicates(["stock_code", "date"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build daily history for CB underlyings.")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    data = build(args.start, args.end)
    out = STORE_DIR / "market" / "stock_daily_for_cb.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".csv.tmp")
    data.to_csv(temp, index=False)
    temp.replace(out)
    metadata = scan_csv(out, "date")
    metadata.update({
        "file": "market/stock_daily_for_cb.csv",
        "category": "market",
        "description": "转债正股日线（Tushare daily，覆盖历史转债正股）",
    })
    register_dataset("stock_daily_for_cb", metadata)
    print(f"rows={len(data)} stocks={data['stock_code'].nunique()} {data['date'].min().date()}..{data['date'].max().date()} output={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

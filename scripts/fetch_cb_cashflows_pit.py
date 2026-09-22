#!/usr/bin/env python3
"""Materialise auditable CB redemption cashflows from Tushare ``cb_call``.

This is an fmdata source script, so its Tushare access is governed by fmdata.
It records only announced maturity-redemption and forced-redemption cashflows.
It deliberately does *not* invent a redemption price for unmatured bonds;
those require a prospectus-level cashflow source.
"""
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


def _load_builder():
    path = FMDATA_ROOT / "scripts" / "build_cb_panel.py"
    spec = importlib.util.spec_from_file_location("cb_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_cashflows(start: str, end: str) -> pd.DataFrame:
    builder = _load_builder()
    raw = builder.fetch_cb_call(TushareFetcher(), start, end)
    columns = [
        "code", "available_date", "cashflow_date", "payment_date",
        "cashflow_type", "redemption_price", "redemption_price_tax",
        "announcement_state", "source", "source_is_pit",
    ]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    frame = raw.copy()
    frame["cashflow_type"] = frame["call_type"].map({"到赎": "MATURITY_REDEMPTION", "强赎": "FORCED_REDEMPTION"})
    frame = frame[frame["cashflow_type"].notna()].copy()
    frame["available_date"] = pd.to_datetime(frame["ann_date"], errors="coerce")
    frame["cashflow_date"] = pd.to_datetime(frame["call_date"], errors="coerce")
    frame["payment_date"] = pd.to_datetime(frame["payment_date"], errors="coerce")
    frame["redemption_price"] = pd.to_numeric(frame["call_price"], errors="coerce")
    frame["redemption_price_tax"] = pd.to_numeric(frame["call_price_tax"], errors="coerce")
    frame["announcement_state"] = frame["is_call"].astype(str)
    frame["source"] = "tushare_cb_call"
    frame["source_is_pit"] = True
    # A state-only notice has no announced cashflow price. Preserve it in
    # cb_events_pit, but do not claim it is an auditable cashflow record.
    frame = frame.dropna(subset=["code", "available_date", "redemption_price"])
    frame = frame[frame["redemption_price"] > 0]
    return (frame[columns]
            .sort_values(["code", "available_date", "cashflow_type"])
            .drop_duplicates(["code", "available_date", "cashflow_type"], keep="last")
            .reset_index(drop=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CB historical announced redemption cashflows.")
    parser.add_argument("--start", default="20100101")
    parser.add_argument("--end", default=pd.Timestamp.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    data = build_cashflows(args.start, args.end)
    out = STORE_DIR / "market" / "cb_cashflows_pit.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".csv.tmp")
    data.to_csv(temp, index=False)
    temp.replace(out)
    maturity = int((data["cashflow_type"] == "MATURITY_REDEMPTION").sum()) if not data.empty else 0
    forced = int((data["cashflow_type"] == "FORCED_REDEMPTION").sum()) if not data.empty else 0
    print(f"rows={len(data)} maturity={maturity} forced={forced} output={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

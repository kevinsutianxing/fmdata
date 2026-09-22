#!/usr/bin/env python3
"""Repair cb_daily.csv after the float->str date bug: re-derive the 3 date
columns and recompute proper YTM, without re-fetching the 15-min market data.

The just-written cb_daily.csv already has correct OHLCV, stock_close, PIT
conversion_price, rating, sector — only maturity/listing/conversion_start
dates and ytm_pct are broken (NaT/NaN). Re-fetch cb_basic (1 call) for the
date + rate_clause + coupon_rate, merge, recompute, write back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util

spec = importlib.util.spec_from_file_location("b", Path(__file__).resolve().parent / "build_cb_panel.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

from fmdata.fetcher import TushareFetcher  # noqa: E402
from fmdata.config import STORE_DIR  # noqa: E402

MARKET = STORE_DIR / "market"


def main() -> int:
    panel = pd.read_csv(MARKET / "cb_daily.csv")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["code"] = panel["code"].astype(str)
    print(f"[info] loaded panel: {len(panel):,} rows, {panel['code'].nunique()} bonds")

    f = TushareFetcher()
    basic = b.fetch_cb_basic(f)  # numeric dates + rate_clause
    bb = basic.drop_duplicates("code").copy()
    terms = pd.DataFrame({
        "code": bb["code"],
        "listing_date": b._ymd_to_ts(bb["list_date"]),
        "maturity_date": b._ymd_to_ts(bb["maturity_date"]),
        "conversion_start_date": b._ymd_to_ts(bb["conv_start_date"]),
        "coupon_rate": pd.to_numeric(bb["coupon_rate"], errors="coerce"),
        "rate_clause": bb.get("rate_clause"),
    })

    # drop broken date + ytm columns, re-merge clean
    panel = panel.drop(columns=["listing_date", "maturity_date",
                                "conversion_start_date", "ytm_pct"], errors="ignore")
    panel = panel.merge(terms[["code", "listing_date", "maturity_date",
                               "conversion_start_date", "coupon_rate", "rate_clause"]],
                        on="code", how="left")

    # recompute proper YTM per bond
    panel["ytm_pct"] = np.nan
    n_bonds = panel["code"].nunique()
    for i, (code, g) in enumerate(panel.groupby("code")):
        mat = g["maturity_date"].iloc[0]
        if pd.isna(mat):
            continue
        sched = b._parse_coupon_schedule(g["rate_clause"].iloc[0]) if "rate_clause" in g else None
        panel.loc[g.index, "ytm_pct"] = b._bond_ytm_series(
            g["date"].values, g["cb_close"].values, b.PAR, mat,
            float(g["coupon_rate"].iloc[0]) if pd.notna(g["coupon_rate"].iloc[0]) else 0.0,
            sched,
        )
        if (i + 1) % 100 == 0:
            print(f"  YTM {i+1}/{n_bonds}")

    # Preserve the same explicit provenance contract as build_cb_panel. The
    # repaired YTM is still an approximation until prospectus cashflows are
    # available and must never silently become a production carry factor.
    panel["redemption_price_source"] = "placeholder_not_prospectus_verified"
    panel["redemption_price_is_approx"] = True
    panel["ytm_source"] = "tushare_rate_clause_annualised_redemption_assumed_par"
    panel["ytm_is_approx"] = True
    panel["cashflow_version"] = "rate_clause_v1_no_prospectus_redemption"
    panel["terms_is_pit"] = False
    panel["terms_available_date"] = pd.NaT

    # restore engine column order
    cols = ["date", "code", "name", "stock_code", "cb_close", "cb_open",
            "amount_million", "remaining_size_million", "stock_close",
            "conversion_price", "redemption_price", "redemption_price_source",
            "redemption_price_is_approx", "ytm_pct", "ytm_source", "ytm_is_approx",
            "cashflow_version", "terms_is_pit", "terms_available_date", "rating", "sector",
            "listing_date", "maturity_date", "conversion_start_date", "source_status"]
    panel = panel[[c for c in cols if c in panel.columns]].sort_values(["code", "date"])

    b.write_csv(panel, "cb_daily.csv")

    # regenerate terms_pit
    terms_pit = b.build_terms_pit(basic)
    b.write_csv(terms_pit, "cb_terms_pit.csv")

    latest = panel["date"].max()
    print(f"[qa] latest={latest.date()} bonds={panel['code'].nunique()} "
          f"ytm={panel['ytm_pct'].notna().mean():.1%} "
          f"ytm median={panel['ytm_pct'].median():.2f}% "
          f"maturity={panel['maturity_date'].notna().mean():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fetch full open-ended fund basic table into fmdata store."""
from pathlib import Path

import pandas as pd
import tushare as ts


OUT = Path("/home/ubuntu/fmdata/store/market/fund_basic_open.csv")
PAGE_SIZE = 5000


def main() -> None:
    pro = ts.pro_api()
    frames = []
    offset = 0
    while True:
        page = pro.query("fund_basic", market="O", limit=PAGE_SIZE, offset=offset)
        if page is None or page.empty:
            break
        frames.append(page)
        print(f"fetched offset={offset} rows={len(page)}")
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    if not frames:
        raise SystemExit("fund_basic returned empty data")
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")
    if df is None or df.empty:
        raise SystemExit("fund_basic returned empty data")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"saved {len(df)} rows to {OUT}")
    print("columns:", ",".join(df.columns))
    for col in ["fund_type", "invest_type", "type", "status"]:
        if col in df.columns:
            print(f"\n{col}")
            print(df[col].value_counts(dropna=False).head(30).to_string())


if __name__ == "__main__":
    main()

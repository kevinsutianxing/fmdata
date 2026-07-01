#!/usr/bin/env python3
"""融资融券明细数据 (上交所+深交所合并)
按交易日遍历最近 N 天。
source: akshare stock_margin_detail_sse / stock_margin_detail_szse
"""
import time
import pandas as pd
from datetime import datetime, timedelta
import akshare as ak

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/margin_detail.csv"
DAYS_BACK = 30
DELAY = 0.5


def daterange(end_date, days):
    end = datetime.strptime(end_date, "%Y%m%d")
    start = end - timedelta(days=days - 1)
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return dates


def fetch_one(date_str):
    parts = []
    for market_func, src_label in [
        (ak.stock_margin_detail_sse, "SSE"),
        (ak.stock_margin_detail_szse, "SZSE"),
    ]:
        try:
            df = market_func(date=date_str)
            if df is not None and len(df) > 0:
                df["SOURCE"] = src_label
                parts.append(df)
        except Exception:
            pass
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True)


def main():
    today = datetime.now().strftime("%Y%m%d")
    dates = daterange(today, DAYS_BACK)
    print(f"[margin_detail] Date range: {dates[0]} ~ {dates[-1]} ({len(dates)} days)", flush=True)

    all_dfs = []
    for i, date_str in enumerate(dates):
        df = fetch_one(date_str)
        if df is not None:
            all_dfs.append(df)
            print(f"  [{date_str}] {len(df)} rows", flush=True)
        time.sleep(DELAY)

    if not all_dfs:
        print("[margin_detail] WARNING: no data collected", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    result = pd.concat(all_dfs, ignore_index=True)
    result.to_csv(OUTPUT_CSV, index=False)
    print(f"[margin_detail] DONE: {len(result)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()

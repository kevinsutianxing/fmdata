#!/usr/bin/env python3
"""Fetch A-share daily and daily_basic one trade date at a time, then atomically merge."""
import os
import time
from pathlib import Path
import pandas as pd
import tushare as ts

OUT = Path.home() / "fmdata" / "store" / "market" / "daily_window_20260822_20260930.csv"
BASIC_OUT = Path.home() / "fmdata" / "store" / "market" / "daily_basic_window_20260822_20260930.csv"
DATES = pd.bdate_range("2026-08-22", "2026-09-30").strftime("%Y%m%d").tolist()

def retry(func, **kwargs):
    for attempt in range(5):
        try:
            return func(**kwargs)
        except Exception as exc:
            message = str(exc)
            if any(key in message for key in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(5 + attempt * 5)
                continue
            raise
    raise RuntimeError("unreachable")

def pull(name):
    pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))
    frames = []
    for date in DATES:
        daily = retry(getattr(pro, name), trade_date=date)
        if daily is not None and not daily.empty:
            frames.append(daily)
            print(f"{name} {date}: {len(daily)}")
        time.sleep(0.15)
    if not frames:
        raise RuntimeError(f"no {name} rows")
    frame = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    return frame.sort_values(["trade_date", "ts_code"])

def atomic(frame, path):
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)
    print(f"{path}: rows={len(frame)} dates={frame.trade_date.min()}..{frame.trade_date.max()}")

def main():
    atomic(pull("daily"), OUT)
    atomic(pull("daily_basic"), BASIC_OUT)

if __name__ == "__main__":
    main()

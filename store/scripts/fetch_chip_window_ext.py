#!/usr/bin/env python3
"""Fetch chip-nowcast raw window extension (moneyflow/daily/daily_basic/adj_factor)
one trade date at a time into chip_window_ext_20261001_20261027/, mirroring
chip_window_20250701_20260930 layout. Backs recipe chip_window_ext_202610."""
import os
import time
from pathlib import Path
import pandas as pd
import tushare as ts
from dotenv import dotenv_values

ENV = dotenv_values(Path.home() / "fmdata" / ".env")
TOKEN = os.environ.get("TUSHARE_TOKEN") or ENV.get("TUSHARE_TOKEN", "")
OUTD = Path.home() / "fmdata" / "store" / "market" / "chip_window_ext_20261001_20261027"
OUTD.mkdir(parents=True, exist_ok=True)
START, END = "20261001", "20261027"
FIELDS = {"daily_basic": "ts_code,trade_date,close,turnover_rate,turnover_rate_f,float_share"}

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

def main():
    pro = ts.pro_api(TOKEN)
    cal = retry(pro.trade_cal, exchange="SSE", start_date=START, end_date=END)
    dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    print(f"trade days {START}->{END}: {len(dates)} {dates[0]}..{dates[-1]}", flush=True)
    for iface in ["moneyflow", "daily", "daily_basic", "adj_factor"]:
        frames = []
        for date in dates:
            kw = dict(trade_date=date)
            if FIELDS.get(iface):
                kw["fields"] = FIELDS[iface]
            df = retry(getattr(pro, iface), **kw)
            if df is not None and not df.empty:
                frames.append(df)
            print(f"  {iface} {date}: {0 if df is None else len(df)}", flush=True)
            time.sleep(0.15)
        if not frames:
            raise RuntimeError(f"no {iface} rows for {START}..{END}")
        frame = (pd.concat(frames, ignore_index=True)
                 .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
                 .sort_values(["trade_date", "ts_code"]))
        temp = OUTD / (iface + ".csv.tmp")
        dest = OUTD / (iface + ".csv")
        frame.to_csv(temp, index=False)
        temp.replace(dest)
        print(f"{dest}: rows={len(frame)} dates={frame.trade_date.min()}..{frame.trade_date.max()}", flush=True)

if __name__ == "__main__":
    main()

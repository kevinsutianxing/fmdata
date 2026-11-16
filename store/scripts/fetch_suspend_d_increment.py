#!/usr/bin/env python3
"""Fetch Tushare suspend_d by trade date and append only new records."""
import os
import time
from pathlib import Path
import pandas as pd
import tushare as ts

OUT = Path.home() / "fmdata" / "store" / "research_export" / "suspend_d.csv"

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

def main() -> None:
    pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))
    existing = pd.read_csv(OUT, dtype=str) if OUT.exists() else pd.DataFrame(columns=["ts_code", "trade_date", "suspend_timing", "suspend_type"])
    known = set(zip(existing["ts_code"], existing["trade_date"]))
    end = pd.Timestamp.today().strftime("%Y%m%d")
    # Keep the rolling window bounded while repairing recent gaps.
    dates = pd.bdate_range("2026-08-01", end).strftime("%Y%m%d").tolist()
    frames = [existing]
    added = 0
    for trade_date in dates:
        frame = retry(pro.suspend_d, trade_date=trade_date)
        if frame is None or frame.empty:
            continue
        rows = frame[[c for c in ["ts_code", "trade_date", "suspend_timing", "suspend_type"] if c in frame.columns]].copy()
        rows = rows[[tuple(row) not in known for row in zip(rows["ts_code"], rows["trade_date"])]]
        if not rows.empty:
            frames.append(rows)
            added += len(rows)
        time.sleep(0.12)
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="first")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUT.with_suffix(".csv.tmp"), index=False)
    OUT.with_suffix(".csv.tmp").replace(OUT)
    print(f"suspend_d rows={len(merged)} added={added} max={merged['trade_date'].max()}")

if __name__ == "__main__":
    main()

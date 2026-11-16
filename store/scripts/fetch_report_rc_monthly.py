#!/usr/bin/env python3
"""Append missing Tushare report_rc month-end snapshots without rewriting history."""
import os
import time
from pathlib import Path
import pandas as pd
import tushare as ts

OUT = Path.home() / "fmdata" / "store" / "fundamentals" / "consensus_history" / "report_rc_full_history.csv"
SNAPSHOTS = ["20260630", "20260731", "20260831", "20260930"]

def main() -> None:
    pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))
    existing_months = set()
    if OUT.exists():
        for chunk in pd.read_csv(OUT, usecols=["snapshot_month"], dtype=str, chunksize=500_000):
            existing_months.update(chunk["snapshot_month"].dropna().unique())
    frames = []
    for snapshot in SNAPSHOTS:
        if snapshot in existing_months:
            continue
        frame = pro.report_rc(report_date=snapshot)
        if frame is None or frame.empty:
            print(f"{snapshot}: 0 rows")
            continue
        frame = frame.copy()
        frame["snapshot_month"] = snapshot
        frames.append(frame)
        print(f"{snapshot}: {len(frame)} rows")
        time.sleep(0.3)
    if not frames:
        print("no new report_rc snapshots")
        return
    new = pd.concat(frames, ignore_index=True)
    new.to_csv(OUT, mode="a", header=not OUT.exists(), index=False)
    print(f"appended rows={len(new)} snapshots={','.join(SNAPSHOTS)}")

if __name__ == "__main__":
    main()

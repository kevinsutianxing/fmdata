#!/usr/bin/env python3
"""
Incrementally fetch historical analyst consensus from tushare report_rc.
Pulls one report_date per run, saves to consensus_history/.
Designed for cron: run every 70 minutes (respecting 1hr rate limit).

Usage:
  python3 fetch_historical_consensus.py              # auto-resume from last date
  python3 fetch_historical_consensus.py --start 20210129  # start from specific date
  python3 fetch_historical_consensus.py --status       # show progress
"""
import tushare as ts
import pandas as pd
import numpy as np
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

STORE = Path.home() / "fmdata" / "store"
HIST_DIR = STORE / "fundamentals" / "consensus_history"
HIST_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = HIST_DIR / "_progress.json"

# Month-end trading dates from 2021-01 to 2025-12
# We'll use approximate month-end dates (last Friday or last trading day)
MONTH_ENDS = [
    # 2021
    "20210129","20210226","20210331","20210430","20210531","20210630",
    "20210730","20210831","20210930","20211029","20211130","20211231",
    # 2022
    "20220128","20220225","20220331","20220429","20220531","20220630",
    "20220729","20220831","20220930","20221031","20221130","20221230",
    # 2023
    "20230131","20230224","20230331","20230428","20230531","20230630",
    "20230731","20230831","20230928","20231031","20231130","20231229",
    # 2024
    "20240131","20240223","20240329","20240430","20240531","20240628",
    "20240731","20240830","20240930","20241031","20241129","20241231",
    # 2025
    "20250124","20250228","20250331","20250430","20250523",
]


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": [], "current_idx": 0}


def save_progress(prog):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(prog, f, indent=2)


def fetch_one_date(pro, report_date):
    """Fetch all analyst reports for one date."""
    try:
        df = pro.report_rc(report_date=report_date)
        if df is not None and len(df) > 0:
            # Save raw data
            out_file = HIST_DIR / f"rc_{report_date}.csv"
            df.to_csv(out_file, index=False)

            # Build consensus: mean EPS by (ts_code, quarter) for this date
            eps_df = df[df['eps'].notna()].copy()
            if len(eps_df) > 0:
                consensus = eps_df.groupby(['ts_code', 'name', 'quarter']).agg(
                    eps_mean=('eps', 'mean'),
                    eps_count=('eps', 'count'),
                    eps_std=('eps', 'std'),
                    np_mean=('np', 'mean'),
                    rating_mode=('rating', lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else None),
                ).reset_index()
                consensus['report_date'] = report_date
                cons_file = HIST_DIR / f"consensus_{report_date}.csv"
                consensus.to_csv(cons_file, index=False)

            return len(df), eps_df['ts_code'].nunique() if len(eps_df) > 0 else 0
        return 0, 0
    except Exception as e:
        return -1, str(e)


def show_status():
    prog = load_progress()
    total = len(MONTH_ENDS)
    done = len(prog["completed"])
    print(f"Progress: {done}/{total} ({done/total*100:.0f}%)")
    print(f"Completed dates: {prog['completed'][:5]}...{prog['completed'][-3:]}")
    print(f"Next: {MONTH_ENDS[prog['current_idx']] if prog['current_idx'] < total else 'DONE'}")

    # Check existing files
    raw_files = list(HIST_DIR.glob("rc_*.csv"))
    cons_files = list(HIST_DIR.glob("consensus_*.csv"))
    print(f"Files: {len(raw_files)} raw, {len(cons_files)} consensus")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start from this date (YYYYMMDD)")
    parser.add_argument("--status", action="store_true", help="Show progress only")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    prog = load_progress()

    # Find starting index
    if args.start:
        if args.start in MONTH_ENDS:
            prog["current_idx"] = MONTH_ENDS.index(args.start)
        else:
            print(f"Date {args.start} not in MONTH_ENDS list")
            return

    if prog["current_idx"] >= len(MONTH_ENDS):
        print("All dates completed!")
        show_status()
        return

    next_date = MONTH_ENDS[prog["current_idx"]]
    if next_date in prog["completed"]:
        # Skip already completed
        prog["current_idx"] += 1
        save_progress(prog)
        print(f"Skipping {next_date} (already done). Next: {MONTH_ENDS[prog['current_idx']] if prog['current_idx'] < len(MONTH_ENDS) else 'DONE'}")
        return

    if args.dry_run:
        print(f"Would fetch: {next_date} (index {prog['current_idx']}/{len(MONTH_ENDS)})")
        return

    pro = ts.pro_api()
    print(f"Fetching report_rc for {next_date}...")
    raw_count, stock_count = fetch_one_date(pro, next_date)

    if raw_count == -1:
        print(f"ERROR: {stock_count}")
        return

    prog["completed"].append(next_date)
    prog["current_idx"] += 1
    save_progress(prog)

    print(f"Done: {next_date} → {raw_count} reports, {stock_count} stocks with EPS")
    print(f"Progress: {len(prog['completed'])}/{len(MONTH_ENDS)}")

    # Check if we should merge all consensus files
    if prog["current_idx"] >= len(MONTH_ENDS):
        print("All done! Merging consensus files...")
        merge_consensus()


def merge_consensus():
    """Merge all individual consensus files into one time-series."""
    files = sorted(HIST_DIR.glob("consensus_*.csv"))
    if not files:
        print("No consensus files to merge.")
        return

    dfs = [pd.read_csv(f) for f in files]
    merged = pd.concat(dfs, ignore_index=True)
    out_file = STORE / "fundamentals" / "consensus_history.csv"
    merged.to_csv(out_file, index=False)
    print(f"Merged: {len(merged)} records from {len(files)} dates → {out_file}")
    print(f"Stocks: {merged['ts_code'].nunique()}, Dates: {merged['report_date'].nunique()}")


if __name__ == "__main__":
    main()

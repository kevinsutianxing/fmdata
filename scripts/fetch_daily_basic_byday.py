#!/usr/bin/env python3
"""
Fix: fetch daily_basic by individual trade_date to bypass Tushare 6000-row limit.
Run on sz81 directly.
"""
import tushare as ts
import pandas as pd
import os, time
from datetime import datetime, timedelta

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
CACHE_DIR = os.path.expanduser('~/fmdata/cache/por_plus')
os.makedirs(CACHE_DIR, exist_ok=True)
OUT_PATH = os.path.expanduser('~/fmdata/store/market/daily_basic_hist.csv')

pro = ts.pro_api(TUSHARE_TOKEN)

# Get all trade dates
print("Fetching trade calendar...")
cal = pro.trade_cal(exchange='SSE', start_date='20200101', end_date='20260522')
trade_dates = sorted(cal[cal['is_open']==1]['cal_date'].tolist())
print(f"Total trading days: {len(trade_dates)}")

# Check if partial cache exists
cache_file = os.path.join(CACHE_DIR, "daily_basic_hist_full.csv")
if os.path.exists(cache_file):
    existing = pd.read_csv(cache_file)
    # Normalize to str: trade_date reads back as int64 (all-digit dates) while the
    # trade_cal list below is str, so an un-normalized set never matches -> the
    # incremental skip in `remaining` would treat every date as unfetched and
    # re-pull the entire history each run.
    fetched_dates = set(existing['trade_date'].astype(str).unique())
    print(f"Existing cache: {len(existing)} rows, {len(fetched_dates)} dates")
else:
    existing = pd.DataFrame()
    fetched_dates = set()

remaining = [d for d in trade_dates if d not in fetched_dates]
print(f"Remaining dates to fetch: {len(remaining)}")

all_dfs = [existing] if len(existing) > 0 else []
batch = []
fail_count = 0

for i, td in enumerate(remaining):
    try:
        df = pro.daily_basic(trade_date=td,
                            fields='ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv')
        if df is not None and len(df) > 0:
            batch.append(df)
        else:
            print(f"  Empty: {td}")
    except Exception as e:
        fail_count += 1
        print(f"  FAIL {td}: {e}")
        if fail_count > 50:
            print("Too many failures, stopping.")
            break
    
    # Save every 50 days as checkpoint
    if (i+1) % 50 == 0 and batch:
        checkpoint = pd.concat(batch, ignore_index=True)
        checkpoint.to_csv(cache_file, index=False)
        print(f"  Checkpoint at {td}: {len(batch)} batches saved, total {len(pd.read_csv(cache_file))} rows")
        batch = []
    
    # Rate limit
    time.sleep(0.25)

# Final concat
if batch:
    all_dfs.append(pd.concat(batch, ignore_index=True))

result = pd.concat(all_dfs, ignore_index=True)
# Normalize trade_date to str: existing cache reads back int64 while the tushare
# fetch is str, so concat yields a mixed column and drop_duplicates can't match
# overlapping rows -> duplicates would persist.
result['trade_date'] = result['trade_date'].astype(str)
result = result.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
result = result.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

# Clean extreme values
for col in ['pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm']:
    result.loc[result[col] > 10000, col] = None
    result.loc[result[col] < -10000, col] = None

result.to_csv(cache_file, index=False)
result.to_csv(OUT_PATH, index=False)

print(f"\nDONE: {len(result)} rows, {result['ts_code'].nunique()} stocks")
print(f"Date range: {result['trade_date'].min()} ~ {result['trade_date'].max()}")
print(f"Dates covered: {result['trade_date'].nunique()} / {len(trade_dates)}")
print(f"Saved to: {cache_file}")
print(f"         : {OUT_PATH}")

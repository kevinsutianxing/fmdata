#!/usr/bin/env python3
"""
Fetch daily_basic by individual trade_date - FIXED version.
Accumulates all data, checkpoint appends properly.
"""
import tushare as ts
import pandas as pd
import os, time, glob

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

# Check existing output
existing_dates = set()
if os.path.exists(OUT_PATH):
    existing = pd.read_csv(OUT_PATH)
    existing['trade_date'] = existing['trade_date'].astype(str).str.replace('-','').str.strip()
    existing_dates = set(existing['trade_date'].unique())
    print(f"Existing: {len(existing)} rows, {len(existing_dates)} dates already fetched")
    del existing  # free memory

remaining = [d for d in trade_dates if str(d) not in existing_dates]
print(f"Remaining dates to fetch: {len(remaining)}")

if not remaining:
    print("All dates already fetched!")
    sys.exit(0)

# Fetch day by day, save each day as individual file
fail_count = 0
for i, td in enumerate(remaining):
    day_file = os.path.join(CACHE_DIR, f"db_{td}.csv")
    if os.path.exists(day_file) and os.path.getsize(day_file) > 100:
        continue  # already fetched this day
    
    try:
        df = pro.daily_basic(trade_date=td,
                            fields='ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv')
        if df is not None and len(df) > 0:
            df.to_csv(day_file, index=False)
        if (i+1) % 50 == 0:
            print(f"  Fetched {i+1}/{len(remaining)} days, latest: {td}")
    except Exception as e:
        fail_count += 1
        print(f"  FAIL {td}: {e}")
        if fail_count > 100:
            print("Too many failures, stopping.")
            break
    time.sleep(0.22)

print(f"\nFetching complete. Combining all day files...")

# Combine all day files
all_files = sorted(glob.glob(os.path.join(CACHE_DIR, "db_*.csv")))
print(f"Day files found: {len(all_files)}")

chunks = []
for f in all_files:
    try:
        chunks.append(pd.read_csv(f))
    except:
        pass

result = pd.concat(chunks, ignore_index=True)
result['trade_date'] = result['trade_date'].astype(str).str.replace('-','').str.strip()
result = result[result['trade_date'].str.match(r'^\d{8}$', na=False)]

# Dedup and sort
result = result.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
result = result.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

# Clean extremes
for col in ['pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm']:
    result.loc[result[col] > 10000, col] = None
    result.loc[result[col] < -10000, col] = None

result.to_csv(OUT_PATH, index=False)
print(f"\nDONE: {len(result)} rows, {result['ts_code'].nunique()} stocks")
print(f"Date range: {result['trade_date'].min()} ~ {result['trade_date'].max()}")
print(f"Dates covered: {result['trade_date'].nunique()} / {len(trade_dates)}")
print(f"Saved to: {OUT_PATH}")

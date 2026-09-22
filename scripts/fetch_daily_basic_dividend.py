#!/usr/bin/env python3
import os, time
from pathlib import Path
import pandas as pd
import tushare as ts

START = os.environ.get('DIV_START', '20130101')
END = os.environ.get('DIV_END', '20260715')
OUT = Path(os.environ.get('DIV_OUT', '~/fmdata/store/market/daily_basic_dividend_chunks')).expanduser()
CACHE = Path(os.environ.get('DIV_CACHE', '~/fmdata/cache/daily_basic_dividend')).expanduser()
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
pro = ts.pro_api(os.environ.get('TUSHARE_TOKEN', ''))
cal = pro.trade_cal(exchange='SSE', start_date=START, end_date=END)
dates = sorted(cal.loc[cal.is_open == 1, 'cal_date'].astype(str).tolist())
print(f'dates={len(dates)} range={dates[0] if dates else None}..{dates[-1] if dates else None}', flush=True)
for i, td in enumerate(dates, 1):
    cache_file = CACHE / f'db_{td}.csv'
    if cache_file.exists() and cache_file.stat().st_size > 100:
        continue
    try:
        df = pro.daily_basic(
            trade_date=td,
            fields='ts_code,trade_date,close,pe_ttm,pb,total_mv,dv_ttm,dv_ratio',
        )
        if df is not None and not df.empty:
            df.to_csv(cache_file, index=False)
        if i % 20 == 0:
            print(f'progress={i}/{len(dates)} date={td}', flush=True)
    except Exception as exc:
        print(f'FAIL {td}: {exc}', flush=True)
    time.sleep(0.25)
files = sorted(CACHE.glob('db_*.csv'))
months = sorted({p.stem[3:9] for p in files})
for month in months:
    parts = []
    for f in sorted(CACHE.glob(f'db_{month}*.csv')):
        try:
            parts.append(pd.read_csv(f))
        except Exception:
            pass
    if not parts:
        continue
    out = pd.concat(parts, ignore_index=True)
    out['trade_date'] = out['trade_date'].astype(str)
    out = out.drop_duplicates(['ts_code', 'trade_date'], keep='last')
    out = out.sort_values(['trade_date', 'ts_code'])
    out.to_csv(OUT / f'daily_basic_dividend_{month}.csv', index=False)
print(f'months={len(months)} files={len(files)} out={OUT}', flush=True)

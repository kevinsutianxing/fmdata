#!/usr/bin/env python3
"""Fetch performance forecast from akshare stock_yjyg_em with column mapping."""
import akshare as ak
import pandas as pd
from datetime import datetime

# Only quarter-end dates work: 0331, 0630, 0930, 1231
quarter_ends = [(3,31),(6,30),(9,30),(12,31)]

# Build list of recent quarters
now = datetime.now()
periods = []
for year in [now.year, now.year - 1]:
    for m, d in quarter_ends:
        qd = datetime(year, m, d)
        if qd <= now:
            periods.append(qd.strftime('%Y%m%d'))

# Take last 4 quarters, deduplicated
periods = sorted(set(periods))[-4:]
print(f"Fetching periods: {periods}")

all_dfs = []
for period in periods:
    try:
        df = ak.stock_yjyg_em(date=period)
        if df is not None and len(df) > 0:
            df['_period'] = period
            all_dfs.append(df)
            print(f"  {period}: {len(df)} rows")
        else:
            print(f"  {period}: empty")
    except Exception as e:
        print(f"  {period}: {e}")

if not all_dfs:
    print("ERROR: No data fetched")
    exit(1)

combined = pd.concat(all_dfs, ignore_index=True)

# Map to existing column format
mapped = pd.DataFrame({
    'SECURITY_CODE': combined['股票代码'].apply(lambda x: str(x).zfill(6)),
    'SECURITY_NAME_ABBR': combined['股票简称'],
    'NOTICE_DATE': combined['公告日期'].astype(str),
    'REPORT_DATE': combined['_period'],
    'PREDICT_AMT_LOWER': pd.to_numeric(combined.get('预测数值', 0), errors='coerce'),
    'PREDICT_AMT_UPPER': pd.to_numeric(combined.get('预测数值', 0), errors='coerce'),
    'ADD_AMP_LOWER': pd.to_numeric(combined.get('业绩变动幅度', 0), errors='coerce'),
    'ADD_AMP_UPPER': pd.to_numeric(combined.get('业绩变动幅度', 0), errors='coerce'),
    'PREDICT_TYPE': combined.get('预告类型', ''),
    'PREYEAR_SAME_PERIOD': pd.to_numeric(combined.get('上年同期值', 0), errors='coerce'),
    'PREDICT_RATIO_LOWER': pd.to_numeric(combined.get('业绩变动幅度', 0), errors='coerce'),
    'PREDICT_RATIO_UPPER': pd.to_numeric(combined.get('业绩变动幅度', 0), errors='coerce'),
    'FORECAST_STATE': combined.get('预告类型', '').apply(
        lambda x: {'预增':'increase','预减':'reduction','略增':'slight_increase',
                   '略减':'slight_decrease','续盈':'continue_profit','扭亏':'turn_profit',
                   '首亏':'first_loss','续亏':'continue_loss'}.get(str(x), 'unknown')
    ),
    'IS_LATEST': 'T',
})

# Deduplicate
mapped = mapped.drop_duplicates(subset=['SECURITY_CODE', 'REPORT_DATE', 'PREDICT_TYPE'], keep='last')

out = '/home/ubuntu/fmdata/store/fundamentals/performance_forecast.csv'
mapped.to_csv(out, index=False)
print(f"Saved {len(mapped)} rows to {out}")

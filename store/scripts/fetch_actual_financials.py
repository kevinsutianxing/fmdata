#!/usr/bin/env python3
"""Fetch actual financials from akshare stock_yjbb_em with column mapping."""
import akshare as ak
import pandas as pd
from datetime import datetime

# Fetch latest quarter data (last 4 quarters for coverage)
periods = []
now = datetime.now()
for q_month, q_day in [(3,31),(6,30),(9,30),(12,31)]:
    # Try recent quarters
    for year_offset in [0, -1]:
        y = now.year + year_offset
        d = datetime(y, q_month, q_day)
        if d <= now:
            periods.append(d.strftime('%Y%m%d'))

# Deduplicate and take last 4
periods = sorted(set(periods))[-4:]

all_dfs = []
for period in periods:
    try:
        df = ak.stock_yjbb_em(date=period)
        df['_period'] = period
        all_dfs.append(df)
        print(f"  Fetched {period}: {len(df)} rows")
    except Exception as e:
        print(f"  Failed {period}: {e}")

if not all_dfs:
    print("ERROR: No data fetched")
    exit(1)

combined = pd.concat(all_dfs, ignore_index=True)

# Map columns to match existing format
def make_secucode(code):
    code = str(code).zfill(6)
    if code.startswith(('0','3')):
        return f"{code}.SZ"
    elif code.startswith('6'):
        return f"{code}.SH"
    elif code.startswith('8') or code.startswith('4'):
        return f"{code}.BJ"
    return f"{code}.SZ"

mapped = pd.DataFrame({
    'SECURITY_CODE': combined['股票代码'].apply(lambda x: str(x).zfill(6)),
    'SECURITY_NAME_ABBR': combined['股票简称'],
    'REPORTDATE': combined['_period'],
    'BASIC_EPS': combined['每股收益'],
    'WEIGHTAVG_ROE': combined['净资产收益率'],
    'SJLTZ': combined['净利润-同比增长'],
    'YSTZ': combined['营业总收入-同比增长'],
    'BPS': combined['每股净资产'],
    'QDATE': combined['最新公告日期'].astype(str),
    'DATATYPE': combined['_period'].apply(lambda p: f"{p[:4]}年 " + 
        {'0331':'一季报','0630':'半年报','0930':'三季报','1231':'年报'}.get(p[4:],'')),
    'DATAYEAR': combined['_period'].str[:4],
    'NOTICE_DATE': combined['最新公告日期'].astype(str),
    'SECUCODE': combined['股票代码'].apply(make_secucode),
})

# Deduplicate by (SECURITY_CODE, REPORTDATE) keeping latest
mapped = mapped.drop_duplicates(subset=['SECURITY_CODE', 'REPORTDATE'], keep='last')

out = '/home/ubuntu/fmdata/store/fundamentals/actual_financials.csv'
mapped.to_csv(out, index=False)
print(f"Saved {len(mapped)} rows to {out}")

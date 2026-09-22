import glob
from pathlib import Path
import pandas as pd

files = sorted(glob.glob('/home/ubuntu/fmdata/store/market/daily_basic_dividend_chunks/daily_basic_dividend_*.csv'))
rows = []
for f in files:
    d = pd.read_csv(f, usecols=['trade_date','total_mv','dv_ttm'])
    d['trade_date'] = d['trade_date'].astype(str)
    d['total_mv'] = pd.to_numeric(d['total_mv'], errors='coerce')
    d['dv_ttm'] = pd.to_numeric(d['dv_ttm'], errors='coerce')
    d = d[d['total_mv'] > 0]
    for date, g in d.groupby('trade_date', sort=True):
        valid = g[g['dv_ttm'].notna()]
        mv_all = g['total_mv'].sum()
        mv_valid = valid['total_mv'].sum()
        div = (valid['dv_ttm'] * valid['total_mv']).sum() / mv_valid if mv_valid else float('nan')
        rows.append({
            'date': date,
            'weighted_dv_ttm': div,
            'n_stocks': len(g),
            'n_dv': len(valid),
            'mv_coverage': mv_valid / mv_all if mv_all else float('nan'),
        })
out = pd.DataFrame(rows).drop_duplicates('date').sort_values('date')
out.to_csv('/home/ubuntu/fmdata/store/market/daily_dividend_factor_agg.csv', index=False)
print(out.shape)
print(out.head(2).to_string(index=False))
print(out.tail(2).to_string(index=False))
print(out['weighted_dv_ttm'].describe().to_string())
print('coverage median', out['mv_coverage'].median())

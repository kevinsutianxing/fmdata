#!/usr/bin/env python3
import pandas as pd, numpy as np, glob, os, json
from pathlib import Path
ROOT=Path('/home/ubuntu/fmdata/store/market/daily_basic_dividend_chunks')
OUT=Path('/home/ubuntu/fmdata/store/market/cross_sectional_factor_returns.csv')
files=sorted(glob.glob(str(ROOT/'*.csv')))
months=[os.path.basename(f).split('_')[-1].split('.')[0] for f in files]
# Process one month at a time; select at month-end, hold through next month.
records=[]; prev_last=None; prev_select={}
for i,f in enumerate(files):
    cur=pd.read_csv(f)
    cur['date']=pd.to_datetime(cur.trade_date.astype(str))
    cur=cur.sort_values(['date','ts_code'])
    dates=cur.date.unique()
    # use previous month-end selection for current month returns
    if prev_last is not None and prev_select:
        all_codes=set(prev_select.get('lowpb',[])) | set(prev_select.get('highdv',[])) | set(prev_select.get('combo',[]))
        hold=cur[cur.ts_code.isin(all_codes)].copy()
        if not hold.empty:
            px=hold.pivot_table(index='date',columns='ts_code',values='close',aggfunc='last').sort_index()
            allret=cur.pivot_table(index='date',columns='ts_code',values='close',aggfunc='last').sort_index().pct_change().mean(axis=1)
            lowpb=px[px.columns.intersection(prev_select.get('lowpb',[]))].pct_change().mean(axis=1) if prev_select.get('lowpb') else pd.Series(dtype=float)
            highdv=px[px.columns.intersection(prev_select.get('highdv',[]))].pct_change().mean(axis=1) if prev_select.get('highdv') else pd.Series(dtype=float)
            combo=px[px.columns.intersection(prev_select.get('combo',[]))].pct_change().mean(axis=1) if prev_select.get('combo') else pd.Series(dtype=float)
            out=pd.DataFrame({'market_ret':allret,'lowpb_ret':lowpb,'highdv_ret':highdv,'combo_ret':combo})
            out['lowpb_excess']=out.lowpb_ret-out.market_ret
            out['highdv_excess']=out.highdv_ret-out.market_ret
            out['combo_excess']=out.combo_ret-out.market_ret
            records.append(out.reset_index().rename(columns={'index':'date'}))
    # select at current month-end for next month
    last=cur[cur.date==cur.date.max()].copy()
    # Exclude invalid/negative valuation, very small names and missing dividend
    last=last[(last.total_mv>0)&(last.close>0)]
    def top_bottom(col, ascending=True):
        x=last.dropna(subset=[col]).copy()
        x=x[x[col]>0]
        if len(x)<100: return []
        x=x.sort_values(col,ascending=ascending)
        n=max(30,int(len(x)*0.2))
        return x.ts_code.iloc[:n].tolist()
    lowpb=top_bottom('pb',True)
    highdv=top_bottom('dv_ttm',False)
    combo=list(set(lowpb)&set(highdv))
    prev_select={'lowpb':lowpb,'highdv':highdv,'combo':combo}
    prev_last=last.date.max()
if records:
    result=pd.concat(records,ignore_index=True).sort_values('date').drop_duplicates('date')
    result.to_csv(OUT,index=False)
    print(result.shape, result.date.min(), result.date.max())
    print(result.tail().to_string(index=False))
else:
    print('no records')

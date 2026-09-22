#!/usr/bin/env python3
import glob,os,pandas as pd,numpy as np
from pathlib import Path
ROOT=Path('/home/ubuntu/fmdata/store/market/daily_basic_dividend_chunks'); OUT=Path('/home/ubuntu/fmdata/store/market/cross_sectional_quality_returns.csv')
files=sorted(glob.glob(str(ROOT/'*.csv'))); prev=None; rec=[]
for f in files:
 cur=pd.read_csv(f); cur['date']=pd.to_datetime(cur.trade_date.astype(str)); cur=cur.sort_values(['date','ts_code'])
 if prev is not None:
  all_codes=set().union(*prev.values())
  hold=cur[cur.ts_code.isin(all_codes)].copy()
  if not hold.empty:
   px=hold.pivot_table(index='date',columns='ts_code',values='close',aggfunc='last').sort_index()
   market=cur.pivot_table(index='date',columns='ts_code',values='close',aggfunc='last').sort_index().pct_change().mean(axis=1)
   out=pd.DataFrame({'market_ret':market})
   for name,codes in prev.items():
    out[name+'_ret']=px[px.columns.intersection(codes)].pct_change().mean(axis=1)
    out[name+'_excess']=out[name+'_ret']-market
   rec.append(out.reset_index().rename(columns={'index':'date'}))
 last=cur[cur.date==cur.date.max()].copy(); last=last[(last.close>0)&(last.total_mv>0)]
 # Exclude smallest 30% by market cap; require positive, non-extreme PE for quality versions.
 mv_cut=last.total_mv.quantile(.30); large=last[last.total_mv>=mv_cut]
 good=large[(large.pe_ttm>0)&(large.pe_ttm<100)]
 def pick(frame,col,asc,nfrac=.2):
  x=frame.dropna(subset=[col]); x=x[x[col]>0].sort_values(col,ascending=asc); n=max(30,int(len(x)*nfrac)); return x.ts_code.iloc[:n].tolist()
 prev={'lowpb_large':pick(large,'pb',True),'lowpb_quality':pick(good,'pb',True),'highdv_large':pick(large,'dv_ttm',False),'combo_quality':list(set(pick(good,'pb',True))&set(pick(good,'dv_ttm',False)))}
if rec:
 out=pd.concat(rec,ignore_index=True).sort_values('date').drop_duplicates('date'); out.to_csv(OUT,index=False); print(out.shape,out.date.min(),out.date.max()); print(out.tail().to_string(index=False))
else: print('none')

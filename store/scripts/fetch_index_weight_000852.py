#!/usr/bin/env python3
import os,sys,time
import pandas as pd
import tushare as ts
OUT=os.path.expanduser("~/fmdata/store/market/index_weight_000852_full.csv")
IDX="000852.SH"
def retry(fn,*a,**kw):
    for i in range(5):
        try:return fn(*a,**kw)
        except Exception as e:
            if any(k in str(e) for k in ("频率","limit","每分钟","积分","credit")) and i<4:
                time.sleep(5+i*5);continue
            raise
ts.set_token(os.environ.get("TUSHARE_TOKEN",""));pro=ts.pro_api()
periods=[]
for y in range(2015,2028):
    periods += [(f"{y}0101",f"{y}0630"),(f"{y}0701",f"{y}1231")]
frames=[]
for s,e in periods:
    d=retry(pro.index_weight,index_code=IDX,start_date=s,end_date=e)
    if d is not None and len(d): frames.append(d);print(s,len(d),file=sys.stderr)
    time.sleep(.3)
if not frames: raise SystemExit("no rows")
d=pd.concat(frames,ignore_index=True).drop_duplicates(["con_code","trade_date"])
d=d[["index_code","con_code","trade_date","weight"]].sort_values(["trade_date","weight"],ascending=[True,False])
tmp=OUT+".tmp";d.to_csv(tmp,index=False);os.replace(tmp,OUT)
print("rows",len(d),"snapshots",d.trade_date.nunique(),"range",d.trade_date.min(),d.trade_date.max(),file=sys.stderr)

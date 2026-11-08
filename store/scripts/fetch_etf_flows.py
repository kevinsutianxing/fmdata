#!/usr/bin/env python3
"""A股观察池ETF 份额(tushare fund_share) + 单位净值(fund_nav) 拉取。

输出 tidy 长表 store/market/etf_flows_a.csv: ts_code,date,fd_share(万份),unit_nav(元)。
监控侧用 Δ份额×单位净值 推导真实申赎流(非成交量推算); 积分内接口, 4只×2接口=8次/刷新。
2026-11-08 建。
"""
import csv
import os
import sys
import time
from datetime import datetime, timezone

import tushare as ts

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
STORE = os.path.expanduser("~/fmdata/store/market")
ETFS = ["510050.SH", "510300.SH", "510500.SH", "588000.SH"]
START = "20200101"
KEY = None
for line in open(os.path.expanduser("~/fmdata/.env")):
    if line.startswith("TUSHARE_TOKEN="):
        KEY = line.strip().split("=", 1)[1]
if not KEY:
    sys.exit("TUSHARE_TOKEN missing")

pro = ts.pro_api(KEY)
rows = {}
for code in ETFS:
    share = pro.fund_share(ts_code=code, start_date=START)
    time.sleep(0.5)
    nav = pro.fund_nav(ts_code=code, start_date=START)
    time.sleep(0.5)
    nav_by_date = {str(r["nav_date"] or r.get("ann_date")): r for r in nav.to_dict("records")}
    for r in share.to_dict("records"):
        d = str(r["trade_date"])
        navrow = nav_by_date.get(d)
        if not navrow:
            continue
        unit = navrow.get("unit_nav")
        if unit in (None, ""):
            continue
        rows[(code, d)] = {"ts_code": code, "date": d,
                           "fd_share": r["fd_share"], "unit_nav": unit}
cols = ["ts_code", "date", "fd_share", "unit_nav"]
allr = sorted(rows.values(), key=lambda x: (x["ts_code"], x["date"]))
path = f"{STORE}/etf_flows_a.csv"
tmp = path + ".tmp"
with open(tmp, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(allr)
os.replace(tmp, path)
print(f"etf_flows_a ok rows={len(allr)} window={allr[0]['date']}..{allr[-1]['date']} fetched_at={NOW}")

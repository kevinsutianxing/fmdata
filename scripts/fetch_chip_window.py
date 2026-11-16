#!/usr/bin/env python3
"""
fetch_chip_window.py — 筹码因子复刻的数据回补（sw 2026-08/09 筹码快照缺失的自行计算用）

拉取全市场日级四表 by trade_date（窗口默认 20250701→20260930，覆盖
2026-07-31 校准锚 + 2026-08-31/09-30 两个产出快照的 252 日回溯窗）：
  daily        OHLC + vol/amount（构造三角分布与成交均价）
  daily_basic  turnover_rate（换手率衰减分母）
  adj_factor   前复权（价格轴一致性，窗口内除权除息修正）
  moneyflow    小/中/大/特大单买卖量额（机构/散户分层，口径与
               申万筹码报告挂单额四档 <4万/4-20万/20-100万/>100万 一致）

断点续跑：每 (dataset, date) 一个 checkpoint 文件，重跑只补缺失。
输出：/data/fmdata/store/market/chip_window_<start>_<end>/{四个csv}
用法：python3 fetch_chip_window.py [start=20250701] [end=20260930]
"""
import os
import sys
import time

import pandas as pd
import tushare as ts

START = sys.argv[1] if len(sys.argv) > 1 else "20250701"
END = sys.argv[2] if len(sys.argv) > 2 else "20260930"

CACHE = os.path.expanduser("~/fmdata/cache/chip_window")
OUT_DIR = f"/data/fmdata/store/market/chip_window_{START}_{END}"
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

FIELDS = {
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    "daily_basic": "ts_code,trade_date,close,turnover_rate,turnover_rate_f,float_share",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "moneyflow": (
        "ts_code,trade_date,buy_sm_vol,buy_sm_amount,buy_md_vol,buy_md_amount,"
        "buy_lg_vol,buy_lg_amount,buy_elg_vol,buy_elg_amount,"
        "sell_sm_vol,sell_sm_amount,sell_md_vol,sell_md_amount,"
        "sell_lg_vol,sell_lg_amount,sell_elg_vol,sell_elg_amount,"
        "net_mf_vol,net_mf_amount"
    ),
}
FUNCS = {
    "daily": "daily",
    "daily_basic": "daily_basic",
    "adj_factor": "adj_factor",
    "moneyflow": "moneyflow",
}

pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))

cal = pro.trade_cal(exchange="SSE", start_date=START, end_date=END)
dates = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
print(f"window {START}->{END}: {len(dates)} trade days", flush=True)


def fetch_one(name, td, tries=3):
    for k in range(tries):
        try:
            df = getattr(pro, FUNCS[name])(trade_date=td, fields=FIELDS[name])
            if df is None:
                return pd.DataFrame()
            return df
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                print(f"  FAIL {name} {td}: {e}", flush=True)
                return None
            time.sleep(1.5 * (k + 1))


todo = [(d, n) for d in dates for n in FIELDS if not os.path.exists(f"{CACHE}/{n}_{d}.csv")]
print(f"remaining calls: {len(todo)} (of {len(dates) * 4})", flush=True)

fails = []
for i, (td, name) in enumerate(todo):
    df = fetch_one(name, td)
    if df is None:
        fails.append((td, name))
        continue
    df.to_csv(f"{CACHE}/{name}_{td}.csv", index=False)
    if (i + 1) % 40 == 0:
        print(f"  {i + 1}/{len(todo)} done ({td} {name})", flush=True)
    time.sleep(0.2)

# ---- 合并输出 ----
for name in FIELDS:
    parts = []
    for d in dates:
        f = f"{CACHE}/{name}_{d}.csv"
        if os.path.exists(f):
            parts.append(pd.read_csv(f, dtype={"trade_date": str}))
    if parts:
        out = pd.concat(parts, ignore_index=True)
        out.to_csv(f"{OUT_DIR}/{name}.csv", index=False)
        print(f"[out] {name}: {len(out)} rows, {out.trade_date.min()}->{out.trade_date.max()}", flush=True)
    else:
        print(f"[out] {name}: NO DATA", flush=True)

print(f"DONE. failures={len(fails)} {fails[:10]}", flush=True)

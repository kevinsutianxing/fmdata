#!/usr/bin/env python3
"""一次性/可复用：按报告期补拉 fina_indicator 落 stock_fina 期次文件（按码批拉）。

背景（2026-09-29）：/market/fundamentals 停在 20260331，20260630 期缺失。
根因：① stock_fina recipe 无 cron（一直手工季更）；② recipe 的 batch_by_period
字段在代码中无实现（静默忽略）；③ 当前 tushare token 档位 fina_indicator
必须带 ts_code，按期全市场查是高阶权限——故按码批拉。
生产脚本例外：允许直连 tushare（见 CLAUDE.md）。

用法: python3 fetch_stock_fina_period.py 20260630
DQ 自检：行数 >= 上季 80%、(ts_code,end_date,ann_date) 去重，否则不落盘。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from fmdata.fetcher import TushareFetcher
from fmdata.reference import stock_list

STORE = Path(__file__).resolve().parents[1] / "store/fundamentals/stock_fina"
FIELDS = "ts_code,ann_date,end_date,roe,or_yoy,netprofit_yoy,dt_netprofit_yoy,q_sales_yoy,roe_dt,roe_yearly"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or len(sys.argv[1]) != 8:
        print("usage: fetch_stock_fina_period.py YYYYMMDD"); return 2
    period = sys.argv[1]
    out = STORE / f"fina_{period}.csv"
    if out.exists():
        print(f"BLOCK: {out} already exists; refusing to overwrite"); return 2

    codes = stock_list()["ts_code"].tolist()
    print(f"batching {len(codes)} codes for period {period}")

    fetcher = TushareFetcher()
    frames, failed = [], []
    for i, code in enumerate(codes, 1):
        try:
            df = fetcher.fina_indicator(ts_code=code, start_date=period, end_date=period, fields=FIELDS)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as e:  # noqa: BLE001 — 单码失败不中断批拉，结尾汇总
            failed.append((code, str(e)[:80]))
        if i % 500 == 0:
            print(f"progress {i}/{len(codes)} got={len(frames)} failed={len(failed)}", flush=True)

    if not frames:
        print("BLOCK: zero codes returned data"); return 1
    raw = pd.concat(frames, ignore_index=True)
    df = (
        raw[raw["end_date"] == period]
        .drop_duplicates(subset=["ts_code", "end_date", "ann_date"])
        .sort_values("ts_code")
        .reset_index(drop=True)
    )
    print(f"raw={len(raw)} filtered+deduped={len(df)} failed_codes={len(failed)}")
    if failed:
        for c, e in failed[:10]:
            print(f"  fail {c}: {e}")

    prior = sorted(STORE.glob("fina_*.csv"))
    if prior:
        prev_n = sum(1 for _ in open(prior[-1])) - 1
        if len(df) < 0.8 * prev_n:
            print(f"BLOCK: {len(df)} < 80% of prior period {prev_n} — suspicious, not writing"); return 1

    out.write_text(df.to_csv(index=False), encoding="utf-8")
    print(f"WROTE {out} rows={len(df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

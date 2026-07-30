#!/usr/bin/env python3
"""拉取沪深300全部历史成分（PIT 超集）的后复权因子 adj_factor，逐只调用 tushare。

用途：strategy.py 用 adj_close = close * adj_factor 计算复权收益，消除除权除息日
被误记为损失的问题（~1-2%/yr 偏差）。

选股域 = index_weight_000300.csv 的全部 con_code（459 只曾进指数的票，含已剔除）。
逐只 pro.adj_factor(ts_code=code, start, end)（该端点按 ts_code 或 trade_date 查，
不能批量；按 ts_code 调 459 次 vs 按 trade_date 调 ~1400 次，前者更省）。
带限频退避；原子写：tmp + os.replace。schema：ts_code, trade_date, adj_factor。
"""
import os
import sys
import time
import pandas as pd
import tushare as ts

OUT_CSV = os.path.expanduser("~/fmdata/store/market/adj_factor_csi300.csv")
WEIGHTS_CSV = os.path.expanduser("~/fmdata/store/market/index_weight_000300.csv")
START, END = "20210801", "20260728"


def _retry(fn, *args, **kwargs):
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(6 + attempt * 6)
                continue
            raise


def main():
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    codes = sorted(pd.read_csv(WEIGHTS_CSV, dtype={"trade_date": str})["con_code"].unique())
    print(f"[adj_factor] {len(codes)} codes from index_weight superset", file=sys.stderr)

    frames = []
    fail = []
    for i, code in enumerate(codes, 1):
        try:
            df = _retry(pro.adj_factor, ts_code=code, start_date=START, end_date=END)
            if df is not None and len(df):
                frames.append(df[["ts_code", "trade_date", "adj_factor"]])
        except Exception as e:
            fail.append((code, repr(e)[:80]))
        if i % 50 == 0 or i == len(codes):
            print(f"  [{i}/{len(codes)}] ok={len(frames)} fail={len(fail)}", file=sys.stderr)
        time.sleep(0.12)

    if not frames:
        print("ERROR: no rows fetched", file=sys.stderr)
        sys.exit(1)

    full = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code", "trade_date"])
    full = full[["ts_code", "trade_date", "adj_factor"]].sort_values(["ts_code", "trade_date"])
    tmp = OUT_CSV + ".tmp"
    full.to_csv(tmp, index=False)
    os.replace(tmp, OUT_CSV)

    print(f"[ok] {len(full)} rows, {full['ts_code'].nunique()} codes, "
          f"{full['trade_date'].min()}..{full['trade_date'].max()} -> {OUT_CSV}", file=sys.stderr)
    if fail:
        print(f"[warn] {len(fail)} codes failed: {fail[:5]}", file=sys.stderr)


if __name__ == "__main__":
    main()

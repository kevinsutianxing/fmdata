#!/usr/bin/env python3
"""拉取沪深300(000300.SH)与中证500(000905.SH)历史成分权重，2015-01 至今，按半年分页。

与 fetch_index_weight_000300.py 同模式：tushare index_weight 单次上限 ~7000 行且按
trade_date 降序，按半年窗口分页取全月度快照。输出 schema 一致：
index_code, con_code, trade_date, weight（weight 为百分数，导出层再除 100）。

原子写：tmp + os.replace。已有文件时跳过（除非 --force）。
"""
import os
import sys
import time
import pandas as pd
import tushare as ts

OUT = {
    "000300.SH": os.path.expanduser("~/fmdata/store/market/index_weight_000300_full.csv"),
    "000905.SH": os.path.expanduser("~/fmdata/store/market/index_weight_000905_full.csv"),
}
START_YEAR = 2015


def _retry(fn, *args, **kwargs):
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(5 + attempt * 5)
                continue
            raise


def main():
    force = "--force" in sys.argv
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    this_year = time.localtime().tm_year
    periods = []
    for y in range(START_YEAR, this_year + 2):
        periods.append((f"{y}0101", f"{y}0630"))
        periods.append((f"{y}0701", f"{y}1231"))

    for index_code, out_csv in OUT.items():
        if os.path.exists(out_csv) and not force:
            print(f"{index_code}: {out_csv} 已存在，跳过", file=sys.stderr)
            continue
        frames = []
        for s, e in periods:
            df = _retry(pro.index_weight, index_code=index_code, start_date=s, end_date=e)
            if df is not None and len(df):
                print(f"  {index_code} {s[:4]}H{'1' if s[4:6]=='01' else '2'}: {len(df)} rows", file=sys.stderr)
                frames.append(df)
            time.sleep(0.3)
        if not frames:
            print(f"ERROR: {index_code} no rows", file=sys.stderr)
            sys.exit(1)
        full = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["con_code", "trade_date"])
        full = full[["index_code", "con_code", "trade_date", "weight"]].copy()
        full = full.sort_values(["trade_date", "weight"], ascending=[True, False])
        tmp = out_csv + ".tmp"
        full.to_csv(tmp, index=False)
        os.replace(tmp, out_csv)
        print(f"{index_code}: {len(full)} rows, {full.con_code.nunique()} stocks, "
              f"{full.trade_date.min()}-{full.trade_date.max()} -> {out_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()

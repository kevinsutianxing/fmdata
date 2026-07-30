#!/usr/bin/env python3
"""拉取沪深300历史成分权重（完整 PIT），按半年分页。

根因：tushare `index_weight` 单次调用上限约 7000 行且按 trade_date 降序返回，
所以"一次拉 2021–2026"的写法只能拿到最近 ~13 个月（看起来像"权限只给 13 个月"，
实际是单次行数上限 + 降序）。按半年窗口分页可取回完整月度快照序列
（2021-01 .. 今，每年 ~12 个快照，每个 300 只）。

输出 schema 与原 recipe 完全一致：index_code, con_code, trade_date, weight
（loader / 下游消费者零改动）。原子写：tmp + os.replace。
"""
import os
import sys
import time
import pandas as pd
import tushare as ts

OUT_CSV = os.path.expanduser("~/fmdata/store/market/index_weight_000300.csv")
INDEX_CODE = "000300.SH"


def _retry(fn, *args, **kwargs):
    """tushare 限频退避：遇到 rate/credit 异常睡 5s 重试，最多 4 次。"""
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 3:
                time.sleep(5 + attempt * 5)
                continue
            raise


def main():
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    # 半年窗口：2021 .. 当前年+1（多取一格防漏最近的）
    periods = []
    for y in range(2021, 2028):
        periods.append((f"{y}0101", f"{y}0630"))
        periods.append((f"{y}0701", f"{y}1231"))

    frames = []
    for s, e in periods:
        df = _retry(pro.index_weight, index_code=INDEX_CODE, start_date=s, end_date=e)
        if df is not None and len(df):
            print(f"  {s[:4]}H{'1' if s[4:6] == '01' else '2'}: {len(df)} rows", file=sys.stderr)
            frames.append(df)
        time.sleep(0.3)

    if not frames:
        print("ERROR: no rows fetched", file=sys.stderr)
        sys.exit(1)

    full = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["con_code", "trade_date"])
    full = full[["index_code", "con_code", "trade_date", "weight"]].copy()
    full = full.sort_values(["trade_date", "weight"], ascending=[True, False])

    tmp = OUT_CSV + ".tmp"
    full.to_csv(tmp, index=False)
    os.replace(tmp, OUT_CSV)

    print(f"[ok] {len(full)} rows, {full['trade_date'].nunique()} snapshots, "
          f"{full['trade_date'].min()}..{full['trade_date'].max()} -> {OUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()

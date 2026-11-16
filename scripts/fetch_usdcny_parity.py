#!/usr/bin/env python3
"""USDCNY 中间价 → store/market/usdcny_central_parity.csv (global-volatility-risk-monitor 件3)。

源: cjpy(天软 TS-OPI) 服务端表「人民币汇率中间价」, code=FXUSDCNY, NAME=美元/人民币。
  原始发布方=中国外汇交易中心(chinamoney/CFETS), 每交易日 09:15~09:50 更新,
  2026-11-07 实测新鲜度: 最新 20261106(=最近交易日), 与 chinamoney 官方 API 逐日一致。
  (旧 BOC 源 2026-08-17 起 stale, 本表替位。)

cjpy 该表每次调用返回全量历史(1994 起 ~8000 行) → 直接幂等全刷, 只保留近 3 年,
  按 trade_date 去重, 原子写。新鲜度闸门: 最新值距今天数 >10 天 → exit 1(cron 告警)。

用法: python3 fetch_usdcny_parity.py
输出列: ts_code, trade_date(YYYYMMDD), central_parity, name, source
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR  # noqa: E402

OUT = STORE_DIR / "market/usdcny_central_parity.csv"
THREE_YEARS_AGO = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
FRESHNESS_LIMIT_DAYS = 10


def main():
    import cjpy
    import pandas as pd

    df = cjpy.get_table_data(code=["FXUSDCNY"], table_name="人民币汇率中间价")
    if df is None or df.empty:
        print("ERROR: cjpy 人民币汇率中间价/FXUSDCNY returned no data", file=sys.stderr)
        return 1
    if "中间价" not in df.columns or "截止日" not in df.columns:
        print(f"ERROR: unexpected columns: {list(df.columns)}", file=sys.stderr)
        return 1

    out = pd.DataFrame({
        "ts_code": "USDCNY",
        "trade_date": df["截止日"].astype(str),
        "central_parity": pd.to_numeric(df["中间价"], errors="coerce"),
        "name": df.get("NAME", "美元/人民币"),
        "source": "cjpy:人民币汇率中间价",
    }).dropna(subset=["central_parity"])
    out = out[out["trade_date"] >= THREE_YEARS_AGO]
    out = out.drop_duplicates("trade_date", keep="last").sort_values("trade_date")

    bad = (~out["central_parity"].between(5.0, 9.0)).mean()
    if bad > 0.01:  # 中间价 5-9 区间外超 1% = 取错序列
        print(f"ERROR: central_parity sanity FAIL ({bad:.0%} outside [5,9]): "
              f"{out['central_parity'].describe().to_dict()}", file=sys.stderr)
        return 1

    latest = out["trade_date"].max()
    age_days = (datetime.now() - datetime.strptime(latest, "%Y%m%d")).days
    if age_days > FRESHNESS_LIMIT_DAYS:
        print(f"ERROR: parity stale: latest {latest} = {age_days}d ago "
              f"(limit {FRESHNESS_LIMIT_DAYS}d) — cjpy 通道疑似断流", file=sys.stderr)
        return 1

    tmp = OUT.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False)
    tmp.rename(OUT)
    print(f"[ok] {OUT} rows={len(out)} range {out['trade_date'].min()}..{latest} "
          f"(age={age_days}d) last={out['central_parity'].iloc[-1]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

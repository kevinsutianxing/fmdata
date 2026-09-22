#!/usr/bin/env python3
"""拉取 tushare 对标因子月末截面,用于与申万金工 16 因子实证对比。

12 个对标因子(每族1-2个) × 2016-01→2026-08 月末 ≈ 1536 次调用,限频 0.35s ≈ 20 分钟。
断点续跑:已有 (factor_name, trade_date) 跳过。
输出: store/factors/ts_compare_factors.csv 长表 (factor_name,ts_code,trade_date,factor_value)
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR

OUT = STORE_DIR / "factors/ts_compare_factors.csv"

# SWHY 复合因子 → tushare 对标(同族代表)
COUNTERPARTS = {
    "估值": ["earnings_to_price", "book_to_market"],
    "低波": ["return_std_63d"],
    "低流动性": ["sum_abs_rtn_amount_20d", "avg_turnover_21d"],
    "动量": ["return_252d"],
    "反转": ["small_cap_reversal_21d"],
    "市值": ["size"],
    "成长": ["yoy_net_profit"],
    "盈利": ["roe_ttm_lag63d"],
    "红利": ["dividend_yield_3y_avg"],
}


def log(msg):
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def main():
    from fmdata.fetcher import TushareFetcher
    tf = TushareFetcher()

    done = set()
    if OUT.exists():
        old = pd.read_csv(OUT, usecols=["factor_name", "trade_date"])
        done = set(map(tuple, old.drop_duplicates().values.tolist()))
        log(f"已有 {len(done)} 个 (factor, date) 组合")

    months = pd.period_range("2016-01", pd.Period.now(freq="M"), freq="M")
    # 交易日月末(日历月末撞周末时 tushare 返 0 行,须取当月最后一个交易日)
    med = pd.read_csv("/tmp/month_end_trade.csv")
    med["p"] = pd.PeriodIndex(pd.to_datetime(med["cal_date"]), freq="M")
    med = dict(zip(med["p"].astype(str), pd.to_datetime(med["cal_date"]).dt.strftime("%Y%m%d")))
    tasks = [(f, med[str(m)]) for m in months if str(m) in med for f in sum(COUNTERPARTS.values(), [])]
    todo = [(f, d) for f, d in tasks if (f, str(d)) not in done]
    log(f"待拉 {len(todo)} 组合")

    buf = []
    quota_hit = False
    for i, (fac, date) in enumerate(todo):
        try:
            df = tf._call("factor_value", None, factor_name=fac, trade_date=date)
        except Exception as e:
            if "频率超限" in str(e):
                # 滚动 24h 配额墙 — 立即熔断保断点,稍后重跑本脚本即续
                log(f"QUOTA_HIT at {fac}@{date}: 配额未重置,熔断退出")
                quota_hit = True
                break
            log(f"ERR {fac}@{date}: {e}, sleep 5 重试")
            time.sleep(5)
            try:
                df = tf._call("factor_value", None, factor_name=fac, trade_date=date)
            except Exception as e2:
                log(f"SKIP {fac}@{date}: {e2}")
                df = None
        if df is not None and not df.empty:
            buf.append(df)
        time.sleep(0.35)
        if (i + 1) % 50 == 0:
            _flush(buf); buf = []
            log(f"进度 {i+1}/{len(todo)}")
    _flush(buf)
    if quota_hit:
        return 2
    log("TS PULL DONE")


def _flush(buf):
    if not buf:
        return
    df = pd.concat(buf, ignore_index=True)
    cols = [c for c in ("factor_name", "ts_code", "trade_date", "factor_value") if c in df.columns]
    df = df[cols]
    if OUT.exists():
        old = pd.read_csv(OUT)
        df = pd.concat([old, df], ignore_index=True)
    df = df.drop_duplicates(subset=["factor_name", "ts_code", "trade_date"], keep="last")
    df.to_csv(OUT.with_suffix(".csv.tmp"), index=False)
    OUT.with_suffix(".csv.tmp").rename(OUT)
    log(f"flush -> {OUT} (total {len(df)})")


if __name__ == "__main__":
    sys.exit(main())

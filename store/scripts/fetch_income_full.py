#!/usr/bin/env python3
"""Fetch full-market 正式财报归母净利润 via akshare stock_yjbb_em (东财业绩报表).

数据源：akshare stock_yjbb_em —— 东财「业绩报表」，按报告期一次拉全市场所有已正式
披露公司的合并报表归母净利润（含金融股，与 performance_forecast/express 口径一致）。
每个报告期 1 次调用，最近 8 季度共 8 次；替代旧版 tushare per-code（~5200 次调用）。

字段映射（akshare → 输出列，保持与旧 tushare 版完全一致的 schema）：
  股票代码            → code (zfill 6)
  最新公告日期        → ann_date (YYYYMMDD)
  <请求的 period>     → end_date (YYYYMMDD, 报告期)
  净利润-净利润        → 归母净利润_元 (元, 东财业绩报表口径=归母)
  营业总收入-营业总收入 → 营收_元

输出：~/fmdata/store/fundamentals/income_full.csv
消费者：earnings-disclosure-report build_report.py 的 fdm_income_core（唯一消费者）。

去重：同 (code, end_date) 多行（如修正公告）取最新 ann_date。覆盖最近 8 季度。
"""
import os
import sys
import datetime as dt
from pathlib import Path

import akshare as ak
import pandas as pd

OUT_CSV = Path.home() / "fmdata/store/fundamentals/income_full.csv"

# 最近 8 个季度末（含上年同期，覆盖报告所需 current + prior-year-same + buffer）
quarter_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
now = dt.datetime.now()
periods = []
for year in [now.year, now.year - 1, now.year - 2]:
    for m, d in quarter_ends:
        qd = dt.datetime(year, m, d)
        if qd <= now:
            periods.append(qd.strftime("%Y%m%d"))
periods = sorted(set(periods))[-8:]
print(f"[1/3] Fetching stock_yjbb_em for periods: {periods}", file=sys.stderr)


def to_ts_code(code: str) -> str:
    """code → ts_code 后缀（60/68/90→SH，8/4→BJ 北交所，其余→SZ）。"""
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "90", "11", "13")):
        return f"{c}.SH"
    if c.startswith(("8", "4", "92")):
        return f"{c}.BJ"
    return f"{c}.SZ"


all_dfs = []
for period in periods:
    try:
        df = ak.stock_yjbb_em(date=period)
        if df is None or len(df) == 0:
            print(f"  {period}: empty (披露窗口未开/无公司披露)", file=sys.stderr)
            continue
        df["_end_date"] = period
        all_dfs.append(df)
        print(f"  {period}: {len(df)} rows", file=sys.stderr)
    except Exception as e:
        # akshare 对「进行中报告期 + 零披露」会撞 data_json["result"]["pages"] 的 NoneType
        # （东财返回 result=null）。这不是错误——披露季早期本来就没公司正式披露，8月起自愈。
        if "NoneType" in str(e) or "non-subscriptable" in str(e):
            print(f"  {period}: empty (akshare NoneType, 零披露期, 8月起自愈)", file=sys.stderr)
            continue
        print(f"  {period}: FAIL {e}", file=sys.stderr)

if not all_dfs:
    print("ERROR: no data fetched (akshare stock_yjbb_em 全部失败)", file=sys.stderr)
    sys.exit(1)

print(f"[2/3] Mapping columns...", file=sys.stderr)
combined = pd.concat(all_dfs, ignore_index=True)

# 公告日期统一成 YYYYMMDD 字符串（akshare 返回 datetime.date 对象）
ann = pd.to_datetime(combined["最新公告日期"], errors="coerce")
ann_str = ann.dt.strftime("%Y%m%d").fillna("")

mapped = pd.DataFrame({
    "code": combined["股票代码"].astype(str).str.zfill(6),
    "ts_code": combined["股票代码"].astype(str).str.zfill(6).apply(to_ts_code),
    "ann_date": ann_str,
    "end_date": combined["_end_date"],
    "归母净利润_元": pd.to_numeric(combined.get("净利润-净利润"), errors="coerce"),
    "营收_元": pd.to_numeric(combined.get("营业总收入-营业总收入"), errors="coerce"),
})
mapped["归母净利润_万元"] = mapped["归母净利润_元"] / 10000

# 过滤无效行（无 code 或 净利润 全空）
mapped = mapped[mapped["code"].str.match(r"^\d{6}$", na=False)]
mapped = mapped.dropna(subset=["归母净利润_元"])

# 同 (code, end_date) 取最新公告（按 ann_date 排序后 keep=last）
mapped = mapped.sort_values("ann_date").drop_duplicates(["code", "end_date"], keep="last")
mapped = mapped.sort_values(["code", "end_date"]).reset_index(drop=True)

print(f"[3/3] saved {len(mapped)} rows ({mapped['code'].nunique()} stocks) to {OUT_CSV}", file=sys.stderr)
print(f"  periods coverage: {mapped.groupby('end_date').size().to_dict()}", file=sys.stderr)

OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
mapped.to_csv(OUT_CSV, index=False)
print(f"OK rows={len(mapped)} stocks={mapped['code'].nunique()}")

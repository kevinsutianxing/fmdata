#!/usr/bin/env python3
"""构建分析师一致预期因子表（长表结构同 ts_factor_value：factor_name,ts_code,trade_date,factor_value）。

数据源全部本地/fmdata，无外网调用：
- analyst_consensus.csv        当前快照（评级分布 + EPS1-4，当年+未来3年）→ 预期水平类
- consensus_panel.csv          月度面板（ts_code×ym, consensus_fy1/fy2, 2017-01→2026-05）→ 预期修正类
- consensus_snapshots/*.csv    周快照（同 analyst_consensus 字段）→ 评级动量类
- GET /market/daily-matrix     最新收盘价 → 前瞻PE

因子清单（7个，两类）：
  预期水平: eps_fg_1y / eps_fg_cagr2 / pe_fwd_1y / rating_buy_ratio / rating_cover
  预期修正: eps_rev_3m / rating_rev_1m

口径注意：
- 增速类因子要求基期 EPS>0（亏损股增速无意义），PE 要求 EPS2>0；
- pe_fwd_1y 价格为 daily-matrix 最新完整日收盘，与 EPS 快照日可能差 1-3 天；
- eps_rev_3m 的 trade_date=面板末月月末；面板 fy1 为滚动最近财年口径（同序列
  跨期比值，作修正信号有效，不跨年比较绝对值）；
- rating_rev_1m = 最新快照与 ~3 周前快照的看多占比差（实际间隔随快照文件浮动）。
原子写：tmp + os.replace。重跑全量覆盖。
"""
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

FUND = Path("~/fmdata/store/fundamentals").expanduser()
SNAP_DIR = FUND / "consensus_snapshots"
OUT_CSV = FUND / "consensus_factors.csv"
DAILY_MATRIX_URL = "http://127.0.0.1:1934/market/daily-matrix"

rows = []  # (factor_name, ts_code, trade_date, factor_value)


def add(factor: str, series: pd.Series, trade_date: int):
    """series: index=ts_code, value=float；丢 NaN/inf。"""
    n0 = len(series)
    s = series.replace([np.inf, -np.inf], np.nan).dropna()
    for ts, v in s.items():
        rows.append((factor, ts, trade_date, round(float(v), 8)))
    print(f"  {factor:<18} {len(s):>5} 只 (丢 {n0 - len(s)} NaN/inf)")


def buy_ratio(df: pd.DataFrame) -> pd.Series:
    """买入占比 = RATING_BUY_NUM/RATING_ORG_NUM（纯"买入"评级，不含增持——
    买入+增持在 A 股几乎恒为 1.0 无区分度，实测纯买入 p10=0/中位 0.67）。"""
    org = pd.to_numeric(df["RATING_ORG_NUM"], errors="coerce")
    buy = pd.to_numeric(df["RATING_BUY_NUM"], errors="coerce").fillna(0)
    codes = df["SECUCODE"] if "SECUCODE" in df.columns else df["ts_code"]
    keep = ~codes.duplicated().values          # 源表含整行复制的重复条目
    return (buy / org).where(org >= 1).set_axis(codes)[keep]


# ── 1. 预期水平类（当前快照 analyst_consensus）──────────────────────────
cur = pd.read_csv(FUND / "analyst_consensus.csv").rename(columns={"SECUCODE": "ts_code"})
cur = cur[~cur["ts_code"].duplicated()].set_index("ts_code")   # 源表含整行复制的重复条目
eps1, eps2, eps3 = (pd.to_numeric(cur[c], errors="coerce") for c in ("EPS1", "EPS2", "EPS3"))
snap_date = int(pd.to_datetime(os.path.getmtime(FUND / "analyst_consensus.csv"), unit="s", utc=True)
                .tz_convert("Asia/Shanghai").strftime("%Y%m%d"))

add("eps_fg_1y", (eps2 / eps1 - 1).where(eps1 > 0), snap_date)              # 当年→次年全国一致预期增速
add("eps_fg_cagr2", ((eps3 / eps1) ** 0.5 - 1).where((eps1 > 0) & (eps3 > 0)), snap_date)
add("rating_buy_ratio", buy_ratio(cur.reset_index()), snap_date)
add("rating_cover", np.log1p(pd.to_numeric(cur["RATING_ORG_NUM"], errors="coerce")), snap_date)

# 前瞻PE：最新收盘 / EPS2
with urllib.request.urlopen(DAILY_MATRIX_URL, timeout=60) as r:
    dm = pd.DataFrame(json.load(r)["data"])[["ts_code", "close"]]
close = dm.set_index("ts_code")["close"]
close = close[~close.index.duplicated()]
px_eps = pd.concat({"close": close, "eps2": eps2}, axis=1, join="inner").dropna()
add("pe_fwd_1y", (px_eps.close / px_eps.eps2).where(px_eps.eps2 > 0), snap_date)  # 价格另注，见 docstring

# ── 2. 预期修正类（月度面板 fy1，末月 vs 前3月）────────────────────────
panel = pd.read_csv(FUND / "consensus_panel.csv")
pv = panel.pivot_table(index="ts_code", columns="ym", values="consensus_fy1", aggfunc="last")
cols = sorted(pv.columns)
last_m = pd.Period(cols[-1], freq="M")
prev_m = last_m - 3
prev_col = next((c for c in reversed(cols) if pd.Period(c, freq="M") <= prev_m), None)
if prev_col:
    fy1_last, fy1_prev = pv[cols[-1]], pv[prev_col]
    rev = (fy1_last / fy1_prev - 1).where(fy1_prev > 0)
    add("eps_rev_3m", rev, int(last_m.end_time.strftime("%Y%m%d")))
    print(f"  eps_rev_3m 窗口: {prev_col} → {cols[-1]}")

# ── 3. 评级动量（周快照：最新 vs ~3周前 看多占比差）────────────────────
snaps = sorted(p.stem.split("_")[1] for p in SNAP_DIR.glob("snapshot_*.csv"))
if len(snaps) >= 2:
    latest = snaps[-1]
    target = pd.Timestamp(latest) - pd.Timedelta(days=21)
    earlier = min((s for s in snaps[:-1] if pd.Timestamp(s) < pd.Timestamp(latest) - pd.Timedelta(days=10)),
                  key=lambda s: abs((pd.Timestamp(s) - target).days), default=None)
    if earlier:
        r_now = buy_ratio(pd.read_csv(SNAP_DIR / f"snapshot_{latest}.csv"))
        r_prev = buy_ratio(pd.read_csv(SNAP_DIR / f"snapshot_{earlier}.csv"))
        gap = (pd.Timestamp(latest) - pd.Timestamp(earlier)).days
        add("rating_rev_1m", (r_now - r_prev).dropna(), int(latest))
        print(f"  rating_rev_1m 窗口: {earlier} → {latest} (间隔 {gap} 天)")

# ── 落盘（原子写）───────────────────────────────────────────────────────
out = pd.DataFrame(rows, columns=["factor_name", "ts_code", "trade_date", "factor_value"])
tmp = OUT_CSV.with_suffix(".csv.tmp")
out.to_csv(tmp, index=False)
os.replace(tmp, OUT_CSV)
print(f"\n✅ {OUT_CSV}  {len(out)} 行, {out.factor_name.nunique()} 因子, "
      f"{out.ts_code.nunique()} 只票, trade_date {sorted(out.trade_date.unique())}")

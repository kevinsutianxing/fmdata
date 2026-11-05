#!/usr/bin/env python3
"""ts202 全库流式初筛: 单遍扫长表(6.65GB) → 每因子×月的 PIT RankIC + 大小盘分域 + 窗口/全史对齐计数。

设计(分块低内存铁律): chunksize=200万行, (factor,month)组跨块用 carry-over 缓冲;
收益字典预载; 市值分域用 sw_raw v2; 输出 per-factor 指标表。
用法: setsid nohup python3 ts202_screen.py >> /data/fmdata/logs/ts202_screen.log 2>&1 &
冒烟: CHUNK_LIMIT=3 python3 ts202_screen.py  (只扫3块验证机器)
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

FSTORE = "/data/fmdata/store/factors"
SRC = f"{FSTORE}/ts_factor_value_v2.csv"
OUT_DIR = "/home/ubuntu/claude-workspace/research/ts202-screen-20261105"
MIN_CROSS = 500
CHUNK = 2_000_000
CHUNK_LIMIT = int(os.environ.get("CHUNK_LIMIT", 0))


def log(m):
    print(f"[{datetime.now().strftime('%F %T')}] {m}", flush=True)


def nxt_ym(y):
    p = pd.Period(str(y), freq="M") + 1
    return f"{p.year:04d}{p.month:02d}"


def spearman(a, b):
    ra = a.rank() - a.rank().mean()
    rb = b.rank() - b.rank().mean()
    d = float((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / np.sqrt(d)) if d > 0 else np.nan


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # 预载: 收益(月→Series) + 市值(月→Series, 大小盘二分)
    mr = pd.read_csv(f"{FSTORE}/monthly_returns.csv", usecols=["ym", "ts_code", "pct_chg"])
    mr["ym"] = mr["ym"].astype(str).str.replace("-", "", regex=False).str.slice(0, 6)
    mr = mr.dropna(subset=["pct_chg"]).groupby(["ym", "ts_code"], as_index=False)["pct_chg"].mean()
    RET = {ym: g.set_index("ts_code")["pct_chg"] for ym, g in mr.groupby("ym")}
    sz = pd.read_csv(f"{FSTORE}/sw_raw_monthend_v2.csv", usecols=["date", "ts_code", "市值"])
    sz["ym"] = sz["date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 6)
    SZ = {ym: g.set_index("ts_code")["市值"] for ym, g in sz.groupby("ym")}

    # 累积器: acc[f][ym] = [ic, ic_big, ic_small]
    acc = {}
    carry = None
    n_rows = 0
    log("streaming ...")
    for i, chunk in enumerate(pd.read_csv(SRC, dtype={"trade_date": str}, chunksize=CHUNK)):
        if CHUNK_LIMIT and i >= CHUNK_LIMIT:
            break
        n_rows += len(chunk)
        if carry is not None:
            chunk = pd.concat([carry, chunk], ignore_index=True)
        last_key = tuple(chunk.iloc[-1][["factor_name", "trade_date"]].tolist())
        is_last = (chunk["factor_name"] == last_key[0]) & (chunk["trade_date"] == last_key[1])
        if is_last.all():
            carry = None
        else:
            carry = chunk[is_last].copy()
            chunk = chunk[~is_last]
        chunk["ym"] = chunk["trade_date"].str[:6]
        for (f, ym), g in chunk.groupby(["factor_name", "ym"]):
            nxt = nxt_ym(ym)
            rv = RET.get(nxt)
            if rv is None:
                continue
            fv = g.dropna(subset=["factor_value"]).set_index("ts_code")["factor_value"]
            if len(fv) < 30:
                continue
            rv2 = rv[~rv.index.duplicated()]
            common = fv.index.intersection(rv2.index)
            if len(common) < MIN_CROSS:
                continue
            a, b = fv[common], rv2[common]
            ic = spearman(a, b)
            rec = acc.setdefault(f, {})
            cell = rec.setdefault(ym, [0, np.nan, np.nan])
            cell[0] += 1  # 该组块数(应为1)
            cell[1] = ic
            z = SZ.get(ym)
            if z is not None:
                z2 = z.reindex(common)
                big = z2 >= z2.median()
                if big.sum() >= 200:
                    cell[2] = spearman(a[big.values], b[big.values])
                if (~big).sum() >= 200:
                    rec[ym][1] = cell[1]  # keep
                    rec.setdefault(f"{ym}#s", [0, spearman(a[~big.values], b[~big.values]), np.nan])
        if i % 5 == 0:
            log(f"chunk {i} rows~{n_rows/1e6:.0f}M factors_so_far={len(acc)}")
    log(f"scan done rows={n_rows/1e6:.1f}M factors={len(acc)}")

    # 汇总出表
    rows = []
    for f, rec in acc.items():
        ics = {ym: v[1] for ym, v in rec.items() if not ym.endswith("#s") and v[1] == v[1]}
        ics_s = {ym[:-2]: v[1] for ym, v in rec.items() if ym.endswith("#s") and v[1] == v[1]}
        s = pd.Series(ics).sort_index()
        early = s[s.index < "202101"]; late = s[s.index >= "202101"]
        ss = pd.Series(ics_s).sort_index()
        ics_b = {ym: v[2] for ym, v in rec.items() if not ym.endswith("#s") and v[2] == v[2]}
        sb = pd.Series(ics_b).sort_index()
        def t(x):
            x = pd.Series(x).dropna()
            return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan
        w_months = int((s.index >= "202101").sum())
        rows.append({
            "factor": f, "n_months_all": len(s), "n_months_win": w_months,
            "IC_all": s.mean() if len(s) else np.nan, "t_all": t(s),
            "IC_win": late.mean() if len(late) else np.nan, "t_win": t(late),
            "t_1620": t(early), "pos_win": (late > 0).mean() if len(late) else np.nan,
            "t_small": t(ss), "t_big": t(sb),
            "sign_consistent": bool((t(sb) == t(sb)) and (t(ss) == t(ss)) and (np.sign(t(sb)) == np.sign(t(ss)) == np.sign(t(s)))),
        })
    out = pd.DataFrame(rows).sort_values("t_win", key=lambda x: -x.abs())
    out.to_csv(f"{OUT_DIR}/ts202_screen_metrics.csv", index=False)
    ser_rows = []
    for f, rec in acc.items():
        for ym, v in rec.items():
            if ym.endswith("#s"):
                continue
            ser_rows.append({"factor": f, "ym": ym, "ic": v[1], "ic_big": v[2],
                             "ic_small": rec.get(f"{ym}#s", [0, np.nan, np.nan])[1]})
    pd.DataFrame(ser_rows).to_csv(f"{OUT_DIR}/ts202_ic_series.csv", index=False)
    log(f"IC series dumped: {len(ser_rows)} rows")
    log(f"SCREEN DONE: {len(out)} factors -> {OUT_DIR}/ts202_screen_metrics.csv")


if __name__ == "__main__":
    main()

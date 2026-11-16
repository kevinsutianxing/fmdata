#!/usr/bin/env python3
"""
compute_chip_factors_replica.py — 申万筹码 4 因子复刻引擎 v2

v1→v2 (0731 校准反馈):
  - 剔除北交所(.BJ): tushare moneyflow 的 BJ 分档买卖量 2026-08 前基本为 0(0731 有 290 只
    inst 系列列 NaN 的根因), 官方 sw 的 BJ 筹码成本也无值
  - moneyflow merge 改 left: mf 缺失日不再丢行情行, 买入档 fillna(0) 已有
  - 增加快照 20260630 (官方有值, 做第二校准日; 窗口起点晚 7 日, 老筹码已衰减 <1%, 无实质影响)
  - 集中度按 PDF 第七节留白展开: 首尾跨度/最大岛/岛和 三种多峰合并 × {点注入,三角} × {总量,机构}
  - 本脚本只输出原始量+诊断列, 截面合成移到 compose_chip_replica.py (口径锁定后再回填)

依据《筹码结构因子_计算方法拆解-20261020》(docs/sw-mcp/reports/):
  成本篇: D_t(p) = (1-T)*D_{t-1}(p) + V*1{p=A}
          分层=买入端替换(机构>=20万: lg+elg / 散户<4万: sm), 衰减分母仍=全市场换手率 T
  分布篇: 三角核(顶点=均价, 两端=低/高, 面积=成交量)
  集中度: S={p: D(p)>=0.5*peak}, Conc=span(S)/全价格区间跨度; ±10% 窗口版分母=0.2P
  截断: 252 交易日

输出: /data/fmdata/store/factors/sw_chip_replica_raw.csv
"""
import sys

import numpy as np
import pandas as pd

WIN_DIR = sys.argv[1] if len(sys.argv) > 1 else "/data/fmdata/store/market/chip_window_20250701_20260930"
SNAPSHOTS = ["20260630", "20260731", "20260831", "20260930"]
LOOKBACK = 252          # 原文明确
MIN_DAYS = 120          # 数据不足上市新股等 → NaN
N_GRID = 400            # 拆解建议 200-500 取中上
OUT = "/data/fmdata/store/factors/sw_chip_replica_raw.csv"

df_daily = pd.read_csv(f"{WIN_DIR}/daily.csv", dtype={"trade_date": str})
df_basic = pd.read_csv(f"{WIN_DIR}/daily_basic.csv", dtype={"trade_date": str})
df_adj = pd.read_csv(f"{WIN_DIR}/adj_factor.csv", dtype={"trade_date": str})
df_mf = pd.read_csv(f"{WIN_DIR}/moneyflow.csv", dtype={"trade_date": str})

df = (df_daily.merge(df_adj, on=["ts_code", "trade_date"], how="left")
             .merge(df_basic[["ts_code", "trade_date", "turnover_rate"]], on=["ts_code", "trade_date"], how="left")
             .merge(df_mf, on=["ts_code", "trade_date"], how="left"))     # v2: left, mf 缺日不丢行情
df["adj_factor"] = df.groupby("ts_code")["adj_factor"].ffill().bfill()
df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

rows = []
codes = [c for c in df["ts_code"].unique() if not c.endswith(".BJ")]
print(f"stocks: {len(codes)} (BJ excluded)", flush=True)

for ci, code in enumerate(codes):
    g = df[df["ts_code"] == code]
    if len(g) < MIN_DAYS:
        continue
    scale = g["adj_factor"] / g["adj_factor"].iloc[-1]     # 锚定末日, 比例型因子对整体缩放不变
    L = (g["low"] * scale).values
    H = (g["high"] * scale).values
    C = (g["close"] * scale).values
    A = np.clip(((g["amount"] * 10.0 / g["vol"]) * scale).values, L, H)  # 均价=千元/(手*0.1)
    T = (g["turnover_rate"] / 100.0).fillna(0.0).clip(0, 0.99).values
    V = (g["vol"] * 100.0).values                          # 股
    B_inst = (g["buy_lg_vol"].fillna(0) + g["buy_elg_vol"].fillna(0)).values * 100.0
    B_ret = g["buy_sm_vol"].fillna(0).values * 100.0
    dates = g["trade_date"].values

    for snap in SNAPSHOTS:
        idx = np.searchsorted(dates, snap, side="right")
        if idx == 0 or dates[idx - 1] != snap:
            continue
        lo = max(0, idx - LOOKBACK)
        if idx - lo < MIN_DAYS:
            continue
        grid = np.geomspace(0.1 * L[lo:idx].min(), 10.0 * H[lo:idx].max(), N_GRID)
        Dp = np.zeros(N_GRID); Pi = np.zeros(N_GRID); Ri = np.zeros(N_GRID)   # 点注入: 总/机构/散户
        Pt = np.zeros(N_GRID); Pti = np.zeros(N_GRID)                         # 三角: 总/机构
        for t in range(lo, idx):
            keep = 1.0 - T[t]
            for D in (Dp, Pi, Ri, Pt, Pti):
                D *= keep
            if V[t] <= 0:               # 停牌/零成交日: 衰减照跑, 注入跳过
                continue
            ia = np.searchsorted(grid, A[t])
            if 0 <= ia < N_GRID:
                Dp[ia] += V[t]; Pi[ia] += B_inst[t]; Ri[ia] += B_ret[t]
            m = (grid >= L[t]) & (grid <= H[t])           # 三角核(按 bin 体积近似再归一)
            if m.any():
                gv = grid[m]
                tri = np.where(gv <= A[t],
                               2 * (gv - L[t]) / max((H[t] - L[t]) * (A[t] - L[t]), 1e-12),
                               2 * (H[t] - gv) / max((H[t] - L[t]) * (H[t] - A[t]), 1e-12))
                tri = np.clip(tri, 0, None); tri /= tri.sum()
                Pt[m] += V[t] * tri; Pti[m] += B_inst[t] * tri
        p = C[idx - 1]
        rev20 = C[idx - 1] / C[idx - 21] - 1.0 if idx >= 21 else np.nan   # 合成剥离用(PDF 05)

        def cost(D):
            s = D.sum()
            return (grid * D).sum() / s if s > 1e-6 else np.nan

        def conc(D):
            """(首尾跨度, 最大岛, 岛和, ±10%窗口) 四种集中度原始量"""
            if D.sum() < 1e-6:
                return np.nan, np.nan, np.nan, np.nan
            nz = D > 0
            full = grid[nz].max() - grid[nz].min()
            if full <= 0:
                return np.nan, np.nan, np.nan, np.nan
            S = np.where(D >= 0.5 * D.max())[0]
            if len(S) == 0:
                return np.nan, np.nan, np.nan, np.nan
            gaps = np.where(np.diff(S) > 1)[0]
            starts = np.r_[S[0], S[gaps + 1]]; ends = np.r_[S[gaps], S[-1]]
            isl = grid[ends] - grid[starts]
            w = (grid >= 0.9 * p) & (grid <= 1.1 * p)
            Dw = np.where(w, D, 0.0)
            c_win = np.nan
            if Dw.sum() > 0:
                M = Dw >= 0.5 * Dw.max()
                if M.any():
                    c_win = (grid[M].max() - grid[M].min()) / (0.2 * p)
            return (grid[S[-1]] - grid[S[0]]) / full, isl.max() / full, isl.sum() / full, c_win

        f_i = cost(Pi) / p
        f_r = cost(Ri) / p
        ca_dp, ci_dp, cs_dp, cw_dp = conc(Dp)
        ca_pi, ci_pi, cs_pi, cw_pi = conc(Pi)
        ca_pt, ci_pt, cs_pt, cw_pt = conc(Pt)
        ca_ti, ci_ti, cs_ti, cw_ti = conc(Pti)
        rows.append({"date": snap, "ts_code": code, "p_last": p, "rev20": rev20,
                     "f_cost_raw": cost(Dp) / p, "f_inst_raw": f_i, "f_ret_raw": f_r,
                     "f_diff_raw": f_i - f_r,
                     "ca_dp": ca_dp, "ci_dp": ci_dp, "cs_dp": cs_dp, "cw_dp": cw_dp,
                     "ca_pi": ca_pi, "ci_pi": ci_pi, "cs_pi": cs_pi, "cw_pi": cw_pi,
                     "ca_pt": ca_pt, "ci_pt": ci_pt, "cs_pt": cs_pt, "cw_pt": cw_pt,
                     "ca_ti": ca_ti, "ci_ti": ci_ti, "cs_ti": cs_ti, "cw_ti": cw_ti})
    if (ci + 1) % 500 == 0:
        print(f"  {ci + 1}/{len(codes)} stocks", flush=True)

r = pd.DataFrame(rows)
print(f"snapshot coverage:\n{r.groupby('date')['ts_code'].count()}")
r.to_csv(OUT, index=False)
print(f"[out] {OUT}: {len(r)} rows")

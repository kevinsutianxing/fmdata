#!/usr/bin/env python3
"""compose_chip_replica.py — 截面合成 (口径经 0630+0731 双日校准锁定)

锁定口径 (双日 spearman vs 官方, 见 calibrate v2 变体矩阵):
  筹码成本_rep      = z(f_ret_raw)                        0.999/0.998 (总成本版 f_cost_raw 0.995/0.984 次优)
  筹码成本差_rep    = z(f_diff_raw)                       0.971/0.984
  机构筹码集中度_rep = 0.5*z(-cs_ti) + 0.5*z(cw_ti)        块级 -0.431/-0.437 与 +0.288/+0.144
                     (PDF 第七节多峰合并留白 → 取岛和; ±10% 块按官方方向取向;
                      16 变体全部 |r|<=0.44 → 官方市值分域调整不可复刻, capped-corr 如实披露)
  筹码合成_rep      = -0.5*z(cost块) + 0.5*z(conc块⊥cost)   +0.737/+0.733 双日稳定
                     (成本块方向=低成本低获利盘压力=好; 不做 rev20 残差化——按 PDF 05 字面残差化
                      掉到 0.509/0.641, 官方合成实测保留 +0.4~0.5 rev20 相关 → 其反转构造与
                      朴素 rev20 不同, 此偏差如实披露)

z = 截面 rank→标准正态; 残差化 β̂ = Σxy/Σxx (z 空间)。
输出: /data/fmdata/store/factors/sw_chip_replica.csv (独立文件, 绝不写 sw_factor_value.csv)
"""
import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

RAW = "/data/fmdata/store/factors/sw_chip_replica_raw.csv"
OFF = "/data/fmdata/store/factors/sw_factor_value.csv"
OUT = "/data/fmdata/store/factors/sw_chip_replica.csv"
SIDECAR = "/data/fmdata/store/factors/sw_chip_replica.provenance.txt"

PROVENANCE = (
    "replica-v2 (2026-10-20) 复刻值, 非申万官方数据, 绝不写入 sw_factor_value.csv。"
    "方法=《筹码结构因子_计算方法拆解》换手率衰减递推(252d截断)+点/三角双注入网格400;"
    "分层=买入端替换(机构=大+特大单买量, 散户=小单买量), 衰减分母=全市场换手率; 北交所剔除"
    "(tushare moneyflow BJ 分档量 2026-08 前缺失, 官方 BJ 亦无值)。"
    "口径经官方 20260630+20260731 双日校准锁定: "
    "筹码成本_rep=z(C散/P)(0.999/0.998; 总成本版 0.995/0.984 次优); "
    "筹码成本差_rep=z(C机/P−C散/P)(0.971/0.984); "
    "机构筹码集中度_rep=½z(−岛和·三角·机构)+½z(±10%窗·三角·机构)"
    "(块级−0.431/−0.437与+0.288/+0.144; 16变体全部|r|≤0.44, 官方市值分域调整不可复刻, capped 如实披露); "
    "筹码合成_rep=−½成本块+½集中度块(对成本正交)(0.737/0.733; 未做rev20残差——PDF字面残差化降至0.51/0.64, "
    "官方合成保留+0.4~0.5 rev20相关, 其反转构造与朴素rev20不同, 偏差如实披露)。"
    "sw 官方发布 8/9 月真值后由 cron d91c0b7d 自动补入官方表, 届时本文件仅作对照。"
)


def z(v):
    v = np.asarray(v, float)
    out = np.full(v.shape, np.nan)
    m = ~np.isnan(v)
    if m.sum() >= 10:
        r = rankdata(v[m])
        out[m] = (r - r.mean()) / r.std()
    return out


def resid(y, x):
    """y − β̂·x, 逐对非 nan, 其余保持 nan"""
    y = np.asarray(y, float)
    x = np.asarray(x, float)
    m = ~(np.isnan(y) | np.isnan(x))
    out = y.copy()
    if m.sum() >= 10:
        b = (x[m] * y[m]).sum() / (x[m] * x[m]).sum()
        out[m] = y[m] - b * x[m]
    return out


raw = pd.read_csv(RAW, dtype={"date": str})
frames = []
for snap, g in raw.groupby("date"):
    cost = z(g["f_ret_raw"])
    diff = z(g["f_diff_raw"])
    conc_blk = 0.5 * z(-g["cs_ti"].values) + 0.5 * z(g["cw_ti"].values)
    combo = -0.5 * cost + 0.5 * resid(conc_blk, cost)   # 成本块负向; 不做 rev20 残差(见 docstring)
    frames.append(pd.DataFrame({
        "date": snap, "ts_code": g["ts_code"].values,
        "筹码成本_rep": cost, "筹码成本差_rep": diff,
        "机构筹码集中度_rep": conc_blk, "筹码合成_rep": combo}))
rep = pd.concat(frames, ignore_index=True)
rep.to_csv(OUT, index=False)
with open(SIDECAR, "w") as f:
    f.write(PROVENANCE + "\n")
print(f"[out] {OUT}: {len(rep)} rows")

# ---- 终校: 4 因子 vs 官方 (0630+0731) ----
sw = pd.read_csv(OFF, dtype={"date": str})
sw["date"] = sw["date"].str.replace("-", "")
for snap in ["20260630", "20260731"]:
    o = sw[sw.date == snap]
    m = o.merge(rep[rep.date == snap], on="ts_code")
    print(f"=== {snap} (n_merge={len(m)}) ===")
    for tgt in ["筹码成本", "筹码成本差", "机构筹码集中度", "筹码合成"]:
        mm = m.dropna(subset=[tgt, tgt + "_rep"])
        r, _ = spearmanr(mm[tgt], mm[tgt + "_rep"])
        print(f"  {tgt}: {r:+.3f} (n={len(mm)})")

print("\ncoverage (non-nan):")
print(rep.groupby("date")[["筹码成本_rep", "筹码成本差_rep",
                           "机构筹码集中度_rep", "筹码合成_rep"]].count())

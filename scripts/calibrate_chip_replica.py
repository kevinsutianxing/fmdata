#!/usr/bin/env python3
"""calibrate_chip_replica.py v2 — 原始量变体矩阵 vs 官方 (0630+0731 双校准日), 逐因子 dropna"""
import pandas as pd
from scipy.stats import spearmanr

sw = pd.read_csv("/data/fmdata/store/factors/sw_factor_value.csv", dtype={"date": str})
sw["date"] = sw["date"].str.replace("-", "")
raw = pd.read_csv("/data/fmdata/store/factors/sw_chip_replica_raw.csv", dtype={"date": str})

CONC = ["ca_dp", "ci_dp", "cs_dp", "cw_dp", "ca_pi", "ci_pi", "cs_pi", "cw_pi",
        "ca_pt", "ci_pt", "cs_pt", "cw_pt", "ca_ti", "ci_ti", "cs_ti", "cw_ti"]
CAND = {"筹码成本": ["f_cost_raw", "f_inst_raw", "f_ret_raw", "f_diff_raw"],
        "筹码成本差": ["f_cost_raw", "f_inst_raw", "f_ret_raw", "f_diff_raw"],
        "机构筹码集中度": CONC + ["rev20"],
        "筹码合成": ["rev20"]}

for snap in ["20260630", "20260731"]:
    off = sw[sw.date == snap][["ts_code", "筹码成本", "筹码成本差", "机构筹码集中度", "筹码合成"]]
    m = off.merge(raw[raw.date == snap], on="ts_code")
    print(f"\n=== {snap} (n_merge={len(m)}) ===")
    for tgt, cands in CAND.items():
        res = []
        for c in cands:
            mm = m.dropna(subset=[tgt, c])
            if len(mm) < 200:
                continue
            r, _ = spearmanr(mm[tgt], mm[c])
            res.append((c, r, len(mm)))
        for c, r, n in sorted(res, key=lambda x: -abs(x[1])):
            print(f"  {tgt} ~ {c}: {r:+.3f} (n={n})")

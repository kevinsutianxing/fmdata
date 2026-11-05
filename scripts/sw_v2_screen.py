#!/usr/bin/env python3
"""SW 新库 v2 策略视角初筛(非回测, 无组合层结论): 44 因子 PIT RankIC + 分窗口 + 大小盘分域
+ 与 R26 现役 8 因子的 IC 相关(找正交增量) + ADJUSTED 筹码分域修复检验 + ML 三兄弟冗余度 + IND_ROT 行业层 IC。

对齐规则: t月末因子 → t+1月收益(防前视); 每期截面 ≥500 只; 大小盘分域=按 SIZE 因子当月中位数二分。
输出: ~/claude-workspace/research/sw-v2-screen-20261104/
"""
import os
import numpy as np
import pandas as pd

FSTORE = "/home/ubuntu/fmdata/store/factors"
ISTORE = "/home/ubuntu/fmdata/store/industry"
OUT = "/home/ubuntu/claude-workspace/research/sw-v2-screen-20261104"
MIN_CROSS = 500
R26 = ["动量", "低波", "盈利", "成长", "估值", "分析师", "红利", "筹码成本"]  # 池内7+池外筹码成本
ML3 = ["GBM量价", "RF量价", "XGB量价"]
CHIP_MAP = {  # sw_chip_monthend_v2 原始列名 -> 短名(d=日频源)
    "CN_ASHARE_CHIP_MONTHLY__HOLDING_PRICE_SPREAD": "筹码成本差d",
    "CN_ASHARE_CHIP__HOLDING_PRICE_SPREAD": "筹码成本差d",
    "CN_ASHARE_CHIP_MONTHLY__HOLDING_COST": "筹码成本d",
    "CN_ASHARE_CHIP__HOLDING_COST": "筹码成本d",
    "CN_ASHARE_CHIP_MONTHLY__INSTITUTION_HOLDING_CONCENTRATION": "机构筹码集中度d",
    "CN_ASHARE_CHIP__INSTITUTION_HOLDING_CONCENTRATION": "机构筹码集中度d",
    "CN_ASHARE_CHIP_MONTHLY__COMPOSITE_CHIP_FACTOR": "筹码合成d",
    "CN_ASHARE_CHIP__COMPOSITE_CHIP_FACTOR": "筹码合成d",
    "CN_ASHARE_CHIP_MONTHLY__ADJUSTED_HOLDING_COST": "筹码成本_调整",
    "CN_ASHARE_CHIP__ADJUSTED_HOLDING_COST": "筹码成本_调整",
    "CN_ASHARE_CHIP_MONTHLY__ADJUSTED_COMPOSITE_CHIP_FACTOR": "筹码合成_调整",
    "CN_ASHARE_CHIP__ADJUSTED_COMPOSITE_CHIP_FACTOR": "筹码合成_调整",
    "CN_ASHARE_CHIP_MONTHLY__INDUSTRY_MV_NEUTRAL_SCORE": "行业市值中性分",
    "CN_ASHARE_CHIP__INDUSTRY_MV_NEUTRAL_SCORE": "行业市值中性分",
}


def spearman(a, b):
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    if len(a) < 30:
        return np.nan
    ra = a.rank() - a.rank().mean()
    rb = b.rank() - b.rank().mean()
    d = float((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / np.sqrt(d)) if d > 0 else np.nan


def nxt_ym(y):
    p = pd.Period(str(y), freq="M") + 1
    return f"{p.year:04d}{p.month:02d}"


def tstat(x):
    x = pd.Series(x).dropna()
    return float(x.mean() / x.std() * np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    os.makedirs(OUT, exist_ok=True)
    print("load raw monthend v2 ...", flush=True)
    v2 = pd.read_csv(f"{FSTORE}/sw_raw_monthend_v2.csv")
    v2["ym"] = v2["date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 6)
    factors = [c for c in v2.columns if c not in ("date", "ts_code", "ym")]

    mr = pd.read_csv(f"{FSTORE}/monthly_returns.csv", usecols=["ym", "ts_code", "pct_chg"])
    mr["ym"] = mr["ym"].astype(str).str.replace("-", "", regex=False).str.slice(0, 6)
    mr = mr.dropna(subset=["pct_chg"]).groupby(["ym", "ts_code"], as_index=False)["pct_chg"].mean()
    ret = mr.set_index(["ym", "ts_code"])["pct_chg"]

    size_piv = v2.pivot_table(index="ym", columns="ts_code", values="市值", aggfunc="first")
    pivs = {f: v2.pivot_table(index="ym", columns="ts_code", values=f, aggfunc="first") for f in factors}
    yms = sorted(pivs["市值"].index)
    ret_ym = sorted(ret.index.get_level_values(0).unique())
    print(f"factor months={len(yms)} return months={len(ret_ym)}", flush=True)

    ic_store = {}
    rows = []
    for f in factors:
        p = pivs[f]
        ics_all, ics_big, ics_small, ics_early, ics_late = [], [], [], [], []
        used_yms = []
        cover = []
        for ym in yms:
            nxt = nxt_ym(ym)
            if nxt not in ret.index.get_level_values(0):
                continue
            if ym not in p.index:
                continue  # ML 因子 2019 起/个别因子晚启动, 无该月截面
            fv = p.loc[ym].dropna()
            rv = ret.loc[nxt]
            rv = rv[~rv.index.duplicated(keep="first")]
            common = fv.index.intersection(rv.index)
            if len(common) < MIN_CROSS:
                continue
            fv, rv2 = fv[common], rv[common]
            ics_all.append(spearman(fv, rv2))
            used_yms.append(ym)
            cover.append(len(common))
            sz = size_piv.loc[ym].reindex(common)
            big, small = sz >= sz.median(), sz < sz.median()
            if big.sum() >= 200:
                ics_big.append(spearman(fv[big.values], rv2[big.values]))
            if small.sum() >= 200:
                ics_small.append(spearman(fv[small.values], rv2[small.values]))
            (ics_early if ym < "202101" else ics_late).append(ics_all[-1])
        s = pd.Series(ics_all).dropna()
        ic_store[f] = pd.Series(ics_all, index=used_yms, dtype=float)
        rows.append({
            "factor": f, "n_months": len(s), "mean_cover": int(np.mean(cover)) if cover else 0,
            "IC": s.mean() if len(s) else np.nan, "ICIR": (s.mean() / s.std() if len(s) > 2 and s.std() > 0 else np.nan),
            "t": tstat(s), "pos%": (s > 0).mean() if len(s) else np.nan,
            "IC_1620": tstat(ics_early), "IC_2126": tstat(ics_late),
            "IC_big_t": tstat(ics_big), "IC_small_t": tstat(ics_small),
            "big_small_gap": (np.nanmean(ics_big) - np.nanmean(ics_small)) if ics_big and ics_small else np.nan,
        })
        print(f"{f}: IC={rows[-1]['IC']:.4f} t={rows[-1]['t']:.2f}" if len(s) else f"{f}: 无有效期", flush=True)

    met = pd.DataFrame(rows).sort_values("t", key=lambda x: -x.abs())
    met.to_csv(f"{OUT}/factor_metrics.csv", index=False)

    # IC 相关 vs R26 现役 8 因子
    base = {f: ic_store.get(f) for f in R26}
    corr_rows = []
    for f in factors:
        row = {"factor": f}
        for b in R26:
            x, y = ic_store.get(f), base[b]
            if x is not None and y is not None:
                j = pd.concat([x, y], axis=1, join="inner").dropna()
                row[f"ρ_{b}"] = (np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1] if len(j) > 6 else np.nan)
            else:
                row[f"ρ_{b}"] = np.nan
        corr_rows.append(row)
    pd.DataFrame(corr_rows).to_csv(f"{OUT}/corr_vs_r26.csv", index=False)

    # ADJUSTED 筹码分域修复检验
    chip_path = f"{FSTORE}/sw_chip_monthend_v2.csv"
    if os.path.exists(chip_path):
        ch = pd.read_csv(chip_path)
        ch = ch.rename(columns={c: CHIP_MAP.get(c, c) for c in ch.columns})
        keep = [c for c in ["机构筹码集中度d", "筹码成本_调整", "筹码合成_调整", "行业市值中性分", "筹码成本d"] if c in ch.columns]
        if keep:
            ch["ym"] = ch["date"].astype(str).str.replace("-", "", regex=False).str.slice(0, 6)
            cpiv = {c: ch.pivot_table(index="ym", columns="ts_code", values=c, aggfunc="first") for c in keep}
            crows = []
            for c in keep:
                ics_b, ics_s = [], []
                for ym in sorted(cpiv[c].index):
                    nxt = nxt_ym(ym)
                    if nxt not in ret.index.get_level_values(0):
                        continue
                    fv = cpiv[c].loc[ym].dropna()
                    rv = ret.loc[nxt]
                    rv = rv[~rv.index.duplicated(keep="first")]
                    common = fv.index.intersection(rv.index)
                    if len(common) < MIN_CROSS:
                        continue
                    fv, rv = fv[common], rv[common]
                    sz = size_piv.loc[ym].reindex(common) if ym in size_piv.index else None
                    if sz is None:
                        continue
                    big, small = sz >= sz.median(), sz < sz.median()
                    if big.sum() >= 200:
                        ics_b.append(spearman(fv[big.values], rv[big.values]))
                    if small.sum() >= 200:
                        ics_s.append(spearman(fv[small.values], rv[small.values]))
                crows.append({"chip": c, "IC_big_t": tstat(ics_b), "IC_small_t": tstat(ics_s),
                              "small_mean_IC": np.nanmean(ics_s) if ics_s else np.nan})
            pd.DataFrame(crows).to_csv(f"{OUT}/chip_size_split.csv", index=False)

    # IND_ROT 行业层 IC
    rot_p, eod_p = f"{ISTORE}/sw_ind_rot.csv", f"{ISTORE}/sw_ind_eod.csv"
    if os.path.exists(rot_p) and os.path.exists(eod_p):
        rot = pd.read_csv(rot_p)
        rot["ym"] = rot["TRADE_DATE"].astype(str).str.slice(0, 6)
        eod = pd.read_csv(eod_p, usecols=lambda c: c in ("TRADE_DATE", "CODE", "CN_ASHARE_IND_EOD__CLOSE"))
        eod["ym"] = eod["TRADE_DATE"].astype(str).str.slice(0, 6)
        mc = eod.groupby(["ym", "CODE"])["TRADE_DATE"].max().reset_index()
        eod = eod.merge(mc, on=["ym", "CODE", "TRADE_DATE"])
        close = eod.pivot_table(index="ym", columns="CODE", values="CN_ASHARE_IND_EOD__CLOSE", aggfunc="last").sort_index()
        retm = close.pct_change()
        score = rot.pivot_table(index="ym", columns="CODE", values="CN_ASHARE_IND_ROT", aggfunc="last").sort_index()
        ics = []
        for ym in score.index[:-1]:
            nxt = nxt_ym(ym)
            if nxt not in retm.index:
                continue
            s, r = score.loc[ym].dropna(), retm.loc[nxt]
            common = s.index.intersection(r.index).intersection(r.dropna().index)
            if len(common) < 20:
                continue
            ics.append(spearman(s[common], r[common]))
        s = pd.Series(ics).dropna()
        pd.DataFrame([{"test": "IND_ROT→行业次月收益", "n": len(s), "IC": s.mean(),
                       "t": tstat(s), "pos%": (s > 0).mean()}]).to_csv(f"{OUT}/ind_rot_test.csv", index=False)

    print("SCREEN DONE", flush=True)


if __name__ == "__main__":
    main()

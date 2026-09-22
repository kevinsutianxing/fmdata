#!/usr/bin/env python3
"""申万金工 16 因子 vs tushare 202 因子库 实证对比。

对比维度:同窗同池 rank IC / ICIR / 年度稳定性 / 映射对重叠相关性。
数据:sw_factor_value.csv(SWHY) + ts_compare_factors.csv(tushare 对标12个)
     + monthly_returns.csv(tushare monthly 月涨跌幅,缺失就地补拉)。
运行前提:两个回填已完成。
"""
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR

F = STORE_DIR / "factors"
RET = F / "monthly_returns.csv"

SW_DIR = {  # 官方方向(正=值大未来好);IC 统一翻成这个向
    "估值": 1, "低波": -1, "低流动性": -1, "分析师": 1, "动量": 1, "反转": -1,
    "市值": -1, "成长": 1, "盈利": 1, "红利": 1,
    "筹码成本差": 1, "筹码成本": 1, "机构筹码集中度": -1, "筹码合成": -1,
    "行业轮动": 1, "GBM量价": 1,
}
MAPPING = {  # SWHY → tushare 对标
    "估值": "earnings_to_price", "低波": "return_std_63d",
    "低流动性": "sum_abs_rtn_amount_20d", "动量": "return_252d",
    "反转": "small_cap_reversal_21d", "市值": "size", "成长": "yoy_net_profit",
    "盈利": "roe_ttm_lag63d", "红利": "dividend_yield_3y_avg",
}


def ensure_returns() -> pd.DataFrame:
    if RET.exists():
        df = pd.read_csv(RET)
        if len(df) > 50000:
            return df
    from fmdata.fetcher import TushareFetcher
    import time
    tf = TushareFetcher()
    # monthly 接口本 token 无权限 → daily+adj_factor 月末截面,后复权 close 自算月收益
    dates = pd.to_datetime(pd.read_csv("/tmp/month_end_trade.csv")["cal_date"]).dt.strftime("%Y%m%d").tolist()
    dates = [d for d in dates if "20151201" <= d <= "20260901"]  # 多拉前一月做基期
    closes, adjs = {}, {}
    for i, d in enumerate(dates):
        try:
            px = tf._call("daily", None, trade_date=d)
            af = tf._call("adj_factor", None, trade_date=d)
            if px is not None and not px.empty and af is not None and not af.empty:
                m = px[["ts_code", "close"]].merge(af[["ts_code", "adj_factor"]], on="ts_code")
                m["ym"] = str(pd.to_datetime(d, format="%Y%m%d").to_period("M"))
                closes[d] = m
        except Exception as e:
            print(f"  daily@{d} err {e}")
        if (i + 1) % 40 == 0:
            print(f"  returns pull {i+1}/{len(dates)}")
        time.sleep(0.35)
    allm = pd.concat(closes.values(), ignore_index=True)
    allm["hclose"] = allm["close"] * allm["adj_factor"]
    piv = allm.pivot_table(index="ym", columns="ts_code", values="hclose")
    rets = piv.pct_change()
    df = rets.stack().rename("pct_chg").reset_index()
    df["trade_date"] = df["ym"].str.replace("-", "")
    df.to_csv(RET, index=False)
    print(f"monthly returns computed: {len(df)} rows, {rets.shape[0]} months")
    return df


def ic_series(factor_wide: pd.DataFrame, rets: pd.DataFrame, min_n=100) -> pd.Series:
    """factor_wide: index=ym(Period str), columns=ts_code;rets: 同构。返回逐月 spearman IC。"""
    out = {}
    common = factor_wide.index.intersection(rets.index)
    for ym in common:
        f = factor_wide.loc[ym].dropna()
        r = rets.loc[ym].reindex(f.index).dropna()
        f = f.reindex(r.index)
        if len(r) >= min_n and f.nunique() > 10:
            out[ym] = spearmanr(f.values, r.values).statistic
    return pd.Series(out)


def summarize(name: str, ics: pd.Series, sign: int) -> str:
    if len(ics) < 24:
        return f"{name:28s} 样本不足({len(ics)}月)"
    v = ics * sign
    t = v.mean() / v.std() * np.sqrt(len(v))
    by_year = v.groupby(v.index.str[:4]).mean()
    yr = " ".join(f"{y[2:]}:{x:+.3f}" for y, x in by_year.items())
    return (f"{name:28s} IC={v.mean():+.4f} ICIR={v.mean()/v.std():+.2f} t={t:+.1f} "
            f"n={len(v)} 正率={(v>0).mean()*100:.0f}% | {yr}")


def main():
    print("== 1. 月度收益准备")
    rets_raw = ensure_returns()
    rets_raw = rets_raw[rets_raw["ts_code"].str.endswith(("SH", "SZ"))]
    if "ym" not in rets_raw.columns:
        rets_raw["ym"] = pd.PeriodIndex(pd.to_datetime(rets_raw["trade_date"], format="%Y%m%d"), freq="M").astype(str)
    rets = rets_raw.pivot_table(index="ym", columns="ts_code", values="pct_chg")
    print(f"   {rets.shape[0]} 月 × {rets.shape[1]} 股")

    print("\n== 2. 申万金工 16 因子 IC(方向已按官方翻正;IC=因子t vs 收益t+1)")
    sw = pd.read_csv(F / "sw_factor_value.csv")
    sw = sw[sw["ts_code"].str.endswith(("SH", "SZ"))]
    sw["ym"] = pd.PeriodIndex(pd.to_datetime(sw["date"]), freq="M").astype(str)
    fwd = rets.shift(-1)  # ym 行的收益挪到上月 → 与因子同 ym 对齐
    sw_stats = {}
    for k, s in SW_DIR.items():
        if k not in sw.columns:
            continue
        w = sw.pivot_table(index="ym", columns="ts_code", values=k)
        ics = ic_series(w, fwd, )
        sw_stats[k] = ics
        print("   " + summarize(k, ics, s))

    print("\n== 3. tushare 对标因子 IC(原始方向)")
    ts = pd.read_csv(F / "ts_compare_factors.csv")
    ts = ts[ts["ts_code"].str.endswith(("SH", "SZ"))]
    ts["ym"] = pd.PeriodIndex(pd.to_datetime(ts["trade_date"], format="%Y%m%d"), freq="M").astype(str)
    print(f"   对标因子 {ts['factor_name'].nunique()} 个, {ts['ym'].nunique()} 月")
    ts_stats = {}
    for k in ts["factor_name"].unique():
        w = ts[ts["factor_name"] == k].pivot_table(index="ym", columns="ts_code", values="factor_value")
        ics = ic_series(w, fwd)
        ts_stats[k] = ics
        print("   " + summarize(k, ics, 1))

    print("\n== 4. 映射对重叠(同期截面 spearman 均值;>0.8=信息基本同源)")
    for swk, tsk in MAPPING.items():
        if swk not in sw_stats or tsk not in ts_stats:
            print(f"   {swk} vs {tsk}: 数据缺")
            continue
        w_sw = sw.pivot_table(index="ym", columns="ts_code", values=swk)
        w_ts = ts[ts["factor_name"] == tsk].pivot_table(index="ym", columns="ts_code", values="factor_value")
        cors = []
        for ym in w_sw.index.intersection(w_ts.index):
            a, b = w_sw.loc[ym].dropna(), w_ts.loc[ym].dropna()
            idx = a.index.intersection(b.index)
            if len(idx) > 100:
                cors.append(spearmanr(a[idx].values, b[idx].values).statistic)
        if cors:
            c = pd.Series(cors)
            print(f"   {swk:8s} vs {tsk:26s}: ρ均值={c.mean():+.2f} (n={len(c)}月)")

    print("\nDONE")


if __name__ == "__main__":
    main()

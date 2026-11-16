#!/usr/bin/env python3
"""IND_ROT(行业轮动4列)月度 catch-up —— sw_v2_monthly_refresh.sh 第 3 步(2026-11-06 立)。

通道: 复用 11-04 的 sw_ind_tables.py(factorlib_query_factor)增量拉 → store/industry/sw_ind_rot.csv
行业级: 只取**完整月末**快照(TRADE_DATE=日历月末 且 该月已完结)——月内进行中快照(如 20261102)
        分数会变,PIT 不安全,按设计排除;断言每新增月恰 30 行业、4 得分列全非空、行业名可映射;
        append 进 store/factors/sw_ind_rot_monthend_v2.parquet(改前备份 .bak.{ym})。
个股级: 对"行业级有而个股级缺"的月,按 checkpoint sw2-indrot-20261106 的 PIT 层级重导
        (v1 broadcast 分组 95.3% → pts in/out 窗口 3.4% → 最近 v1 在册月 carry 1.2%,
         逻辑逐段移植 materialize2.py),追加进 csi1800-enhancement/data/sw2_indrot_stock_*.parquet
        (备份 .bak.{ym})。当月 1800 网格 = spine_1800 parquet 优先(cons7 每月 7 日扩),
        缺月时用指数权重并集(300∪500∪1000, 恰 1800)现算;两者都没有 → DEFER 非失败
        (cons7 扩脊柱后重跑本脚本即补);但缺口月落后 >1 月(脊柱链断)→ 告警退出。
幂等: 目标月已在对应 parquet → no-op 退出 0;拉取层按 CSV 已有 TRADE_DATE 跳过。
失败(断言违例/该到未到/意外异常)→ 非零退出,由 sw_v2_monthly_refresh.sh 的 run_pull 发飞书告警。
用法: python3 sw_indrot_monthly_catchup.py  (无参数,每次全量扫描缺口;TZ 钉 Asia/Shanghai)
"""
import glob
import os
import shutil
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata/scripts")
sys.path.insert(0, "/home/ubuntu/fmdata")
from sw_ind_tables import pull_table, OUTDIR  # 复用通道: 5000行超限自动对半降级 + CSV 断点续跑
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

CSI1800_DATA = os.path.expanduser("~/claude-workspace/csi1800-enhancement/data")
MKT = os.path.expanduser("~/fmdata/store/market")
PTS_CSV = os.path.expanduser("~/fmdata/store/fundamentals/sw_industry_pts.csv")
V1_CSV = os.path.expanduser("~/fmdata/store/factors/sw_factor_value.csv")
IND_PQ = os.path.expanduser("~/fmdata/store/factors/sw_ind_rot_monthend_v2.parquet")
SRC_COLS = ["CN_ASHARE_IND_ROT", "CN_ASHARE_IND_ROT__FUND",
            "CN_ASHARE_IND_ROT__CAP", "CN_ASHARE_IND_ROT__MOM"]
RENAME = {"CN_ASHARE_IND_ROT": "IND_ROT", "CN_ASHARE_IND_ROT__FUND": "IND_ROT_FUND",
          "CN_ASHARE_IND_ROT__CAP": "IND_ROT_CAP", "CN_ASHARE_IND_ROT__MOM": "IND_ROT_MOM"}
SCORE_COLS = list(RENAME.values())
MIN_NONNULL = 1790   # 历史参照 1799/1800(0.11% NaN);carry 主导的新月放宽到 10 票


def log(msg):
    print(f"[{time.strftime('%F %T')}] {msg}", flush=True)


def die(msg):
    print(f"[FAIL] {msg}", flush=True)
    sys.exit(1)


def month_end(ym):
    return pd.Period(f"{ym[:4]}-{ym[4:]}", freq="M").end_time.strftime("%Y%m%d")


def next_month(ym):
    y, m = int(ym[:4]), int(ym[4:]) + 1
    if m > 12:
        y, m = y + 1, 1
    return f"{y}{m:02d}"


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[4:]) - 1
    if m < 1:
        y, m = y - 1, 12
    return f"{y}{m:02d}"


def single_glob(pattern):
    hits = sorted(glob.glob(pattern))
    if len(hits) != 1:
        die(f"parquet 定位异常({len(hits)}): {hits}")
    return hits[0]


# ── ① 行业级: 拉 → 完整月末筛选 → 断言 → append ──────────────────────
def catchup_industry():
    ind = pd.read_parquet(IND_PQ)
    have_ym = sorted(set(ind.ym))
    last_ym = max(have_ym)
    today = time.strftime("%Y%m%d")
    cur_ym = today[:6]

    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    start = f"{next_month(last_ym)}01"
    if start <= today:
        pull_table(cli, "CN_ASHARE_INDUSTRY/CN_ASHARE_IND_ROT", "sw_ind_rot.csv",
                   start, today, False)

    stored = pd.read_csv(OUTDIR / "sw_ind_rot.csv", dtype={"TRADE_DATE": str, "CODE": str})
    me = stored[stored.TRADE_DATE.map(lambda d: d == month_end(d[:6]))]  # 日历月末
    me = me[me.TRADE_DATE.str[:6] < cur_ym]                              # 已完结月(排除进行中快照)
    new_ym = sorted(set(me.TRADE_DATE.str[:6]) - set(have_ym))

    # 该到未到守门: 上一完结月缺 = 发布延迟或通道故障 → 重试一次, 仍缺则告警
    due_ym = prev_month(cur_ym)
    if due_ym not in set(have_ym) | set(new_ym):
        log(f"due {due_ym} 缺失, 60s 后重拉一次")
        time.sleep(60)
        pull_table(cli, "CN_ASHARE_INDUSTRY/CN_ASHARE_IND_ROT", "sw_ind_rot.csv",
                   f"{due_ym}01", today, False)
        stored = pd.read_csv(OUTDIR / "sw_ind_rot.csv", dtype={"TRADE_DATE": str, "CODE": str})
        me = stored[stored.TRADE_DATE.map(lambda d: d == month_end(d[:6]))]
        me = me[me.TRADE_DATE.str[:6] < cur_ym]
        new_ym = sorted(set(me.TRADE_DATE.str[:6]) - set(have_ym))
        if due_ym not in new_ym:
            die(f"上一完结月 {due_ym} 月末分仍未发布/不可得(通道故障或发布延迟>D+4)")

    if not new_ym:
        log(f"[NO-OP] 行业级无缺月(已含 {have_ym[0]}..{last_ym} 共 {len(have_ym)} 月)")
        return ind

    pts = pd.read_csv(PTS_CSV, dtype={"stock_code": str, "in_date": str, "out_date": str})
    l1map = pts.groupby("l1_code")["l1_name"].agg(lambda s: s.mode().iat[0]).to_dict()
    parts = []
    for ym in new_ym:
        sub = me[me.TRADE_DATE == month_end(ym)].copy()
        if len(sub) != 30:
            die(f"{ym}: 行业数 {len(sub)} != 30")
        sub["industry_code"] = sub["CODE"]
        sub["industry_name"] = sub["industry_code"].map(l1map)
        if sub.industry_name.isna().any() or sub.duplicated("industry_code").any():
            die(f"{ym}: 行业名映射失败或行业代码重复")
        if not sub[SRC_COLS].notna().all().all():
            die(f"{ym}: 4 得分列存在空值")
        log(f"{ym}: +30 行业(总得分范围 {sub.CN_ASHARE_IND_ROT.min():.2f}..{sub.CN_ASHARE_IND_ROT.max():.2f})")
        parts.append(sub[["industry_code", "industry_name", "ym"] + SRC_COLS].rename(columns=RENAME))

    add = pd.concat(parts, ignore_index=True).sort_values(["ym", "industry_code"]).reset_index(drop=True)
    shutil.copy2(IND_PQ, f"{IND_PQ}.bak.{new_ym[0]}")
    ind_new = pd.concat([ind, add], ignore_index=True)
    ind_new.to_parquet(IND_PQ, index=False)
    log(f"[OK] 行业级 +{len(add)} 行({','.join(new_ym)}): {len(ind_new)} 行/{ind_new.ym.nunique()} 月, "
        f"备份 .bak.{new_ym[0]}")
    return ind_new


# ── ② 个股级: 缺月重导(v1grp→ptswin→carry, 移植 materialize2.py) ──────
def spine_codes(ym):
    """当月 1800 网格: spine parquet 优先(cons7 每月7日扩); 缺月时指数权重并集现算; 都无 → None。"""
    sp = pd.read_parquet(single_glob(f"{CSI1800_DATA}/spine_1800_*.parquet"))
    codes = sp.loc[sp.ym == ym, "ts_code"].tolist()
    if len(codes) == 1800:
        return codes, "spine-parquet"
    union = set()
    for f in ("index_weight_000300_full.csv", "index_weight_000905_full.csv",
              "index_weight_000852_full.csv"):
        d = pd.read_csv(f"{MKT}/{f}", dtype=str)
        ycol = "ym" if "ym" in d.columns else "trade_date"
        if ycol == "trade_date":
            d["ym"] = d["trade_date"].str[:6]
            ycol = "ym"
        union |= set(d.loc[d[ycol] == ym, "con_code"])
    if len(union) == 1800:
        return sorted(union), "index-weights"
    return None, ""


def assign_l1(target_ym, codes, me_dates):
    """(ym,ts_code)→l1 PIT 归属, 三层级与 materialize2.py 逐段一致。"""
    pts = pd.read_csv(PTS_CSV, dtype={"stock_code": str, "in_date": str, "out_date": str})
    pts["out_f"] = pts["out_date"].fillna("99999999")
    pts["ts_code"] = pts["stock_code"].map(
        lambda c: c + (".SH" if c.startswith("6") else ".BJ" if c.startswith(("4", "8")) else ".SZ"))
    pts_l1 = pts.set_index("ts_code")[["in_date", "out_f", "l1_code"]]

    v1 = pd.read_csv(V1_CSV, usecols=["date", "ts_code", "行业轮动"], dtype={"date": str})
    v1["ym"] = v1.date.str[:7].str.replace("-", "")
    v1 = v1[v1.ym.isin(me_dates) & v1["行业轮动"].notna()]
    v1["grp"] = v1.groupby("ym", sort=False)["行业轮动"].transform(lambda s: pd.factorize(s)[0])
    vv = v1.merge(pts[["ts_code", "in_date", "out_f", "l1_code"]], on="ts_code", how="left")
    vv["D"] = vv.ym.map(me_dates)
    vv["ok"] = vv.l1_code.notna() & (vv.in_date <= vv.D) & (vv.out_f > vv.D)
    maj = (vv[vv.ok].groupby(["ym", "grp", "l1_code"]).size().reset_index(name="n")
           .sort_values("n").groupby(["ym", "grp"]).tail(1)
           .rename(columns={"l1_code": "g_l1"})[["ym", "grp", "g_l1"]])
    grp_ref = v1[["ym", "ts_code", "grp"]].merge(maj, on=["ym", "grp"], how="left")

    assign = pd.DataFrame({"ym": target_ym, "ts_code": codes})
    assign["D"] = me_dates[target_ym]
    assign = assign.merge(grp_ref[["ym", "ts_code", "g_l1"]], on=["ym", "ts_code"], how="left")
    assign["src"] = None
    assign.loc[assign.g_l1.notna(), "src"] = "v1grp"
    need = assign.g_l1.isna()
    pl = pts_l1.reindex(assign.loc[need, "ts_code"]).reset_index(drop=True)
    pl.index = assign.index[need]
    okwin = (pl.l1_code.notna() & (pl.in_date <= assign.loc[need, "D"].values)
             & (pl.out_f > assign.loc[need, "D"].values))
    assign.loc[need, "g_l1"] = np.where(okwin, pl.l1_code, None)
    assign.loc[assign.g_l1.notna() & assign.src.isna(), "src"] = "ptswin"
    _lk = grp_ref.dropna(subset=["g_l1"])[["ym", "ts_code", "g_l1"]]
    last_known = {tc: dict(zip(g.ym, g.g_l1)) for tc, g in _lk.groupby("ts_code")}

    def nearest(tc):
        h = last_known.get(tc)
        if not h:
            return None
        ys = sorted(h.keys(), key=lambda y: (abs(int(y) - int(target_ym)), -int(y)))
        return h[ys[0]]

    need2 = assign.g_l1.isna()
    assign.loc[need2, "g_l1"] = [nearest(t) for t in assign.loc[need2, "ts_code"]]
    assign.loc[assign.g_l1.notna() & assign.src.isna(), "src"] = "carry"
    return assign


def catchup_stock(ind):
    stock_pq = single_glob(f"{CSI1800_DATA}/sw2_indrot_stock_*.parquet")
    stock = pd.read_parquet(stock_pq, columns=["ym", "ts_code"])
    targets = [ym for ym in sorted(set(ind.ym))
               if ym > "202012" and ym not in set(stock.ym)]
    if not targets:
        log(f"[NO-OP] 个股级无缺月(已含 {stock.ym.min()}..{stock.ym.max()} 共 {stock.ym.nunique()} 月)")
        return
    me_dates = {p.strftime("%Y%m"): p.end_time.strftime("%Y%m%d")
                for p in pd.period_range("2021-01", max(targets), freq="M")}
    ind_scores = ind[["ym", "industry_code"] + SCORE_COLS]
    deferred = []
    for ym in targets:
        codes, src = spine_codes(ym)
        if codes is None:
            deferred.append(ym)
            log(f"[DEFER] {ym} 个股级: spine 与指数权重均未含该月(cons7 每月 7 日扩脊柱后重跑即补)")
            continue
        assign = assign_l1(ym, codes, me_dates)
        st = assign.merge(ind_scores, left_on=["ym", "g_l1"],
                          right_on=["ym", "industry_code"], how="left")
        st_out = st[["ym", "ts_code"] + SCORE_COLS].sort_values(
            ["ym", "ts_code"]).reset_index(drop=True)
        nn = int(st_out.IND_ROT.notna().sum())
        if len(st_out) != 1800 or nn < MIN_NONNULL:
            die(f"{ym} 个股级: rows={len(st_out)} non-null={nn} (断言 1800 行且 ≥{MIN_NONNULL})")
        log(f"{ym}: +1800 行 non-null={nn} (网格源={src}, 归属 "
            + ",".join(f"{k}:{v}" for k, v in st.src.value_counts(dropna=False).items()) + ")")
        shutil.copy2(stock_pq, f"{stock_pq}.bak.{ym}")
        stock = pd.concat([stock, st_out], ignore_index=True)
        stock.to_parquet(stock_pq, index=False)
    if stock.ym.nunique() > 0 and not deferred:
        log(f"[OK] 个股级: {stock.ym.min()}..{stock.ym.max()} 共 {stock.ym.nunique()} 月")
    overdue = [ym for ym in deferred if ym < prev_month(time.strftime("%Y%m"))]
    if overdue:
        die(f"个股级缺口 {overdue} 落后超 1 月(spine/权重链断裂, 查 cons7 与 index_weight cron)")


def main():
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    log(f"IND_ROT catchup start (tz={time.strftime('%Z')})")
    ind = catchup_industry()
    catchup_stock(ind)
    log("IND_ROT catchup done")


if __name__ == "__main__":
    main()

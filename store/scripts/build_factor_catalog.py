#!/usr/bin/env python3
"""构建统一因子目录 factor_catalog.csv —— 四源因子库的对外统一清单。

数据源与读取方式（除 cjpy 目录实时拉取外全部本地，无外网）：
  sw   申万金工16因子      ← factors/sw_factor_value.csv 表头列（自动感知新因子列）
  ts   tushare试用202因子  ← store/ts_factor_catalog.json（官方文档提取的静态目录）
  cons 一致预期7因子       ← fundamentals/consensus_factors.csv factor_name 去重
  cjpy 长江天软因子        ← cjpy.list_factors()（服务端目录，实时）

输出：store/factors/factor_catalog.csv
列：factor_id,factor_name,source,category_cn,frequency,format,universe,
    history_start,pit_status,query_method,description,warning
factor_id 命名空间：<source>.<factor_name>（sw.动量 / ts.size / cons.eps_fg_1y / cjpy.xxx）
统一查询入口：POST /fetch/factor_query（见 factor_query.py）。
原子写：tmp + os.replace。重跑全量覆盖。
"""
import json
import os
from pathlib import Path

import pandas as pd

STORE = Path("~/fmdata/store").expanduser()
OUT_CSV = STORE / "factors/factor_catalog.csv"

SW_CSV = STORE / "factors/sw_factor_value.csv"
TS_CATALOG = STORE / "ts_factor_catalog.json"
CONS_CSV = STORE / "fundamentals/consensus_factors.csv"

# sw 16 因子的类别归属（10风格+4筹码+行业轮动+GBM量价，来源 docs/sw-mcp/RUNBOOK.md）
SW_CATEGORY = {}
for _f in ["估值", "低波", "低流动性", "分析师", "动量", "反转", "市值", "成长", "盈利", "红利"]:
    SW_CATEGORY[_f] = "风格"
for _f in ["筹码成本差", "机构筹码集中度", "筹码成本", "筹码合成"]:
    SW_CATEGORY[_f] = "筹码"
SW_CATEGORY["行业轮动"] = "行业轮动"
SW_CATEGORY["GBM量价"] = "机器学习量价"

# cons 7 因子的类别（来源 consensus_factor_catalog.json）
CONS_CATEGORY = {
    "eps_fg_1y": "预期水平", "eps_fg_cagr2": "预期水平", "pe_fwd_1y": "预期水平",
    "rating_buy_ratio": "预期水平", "rating_cover": "预期水平",
    "eps_rev_3m": "预期修正", "rating_rev_1m": "预期修正",
}

FACADE = "facade: 改 recipes/factor_query.yaml 的 fetch.params 后 POST /fetch/factor_query"

rows = []


def trunc(s, n=160):
    s = " ".join(str(s).split())
    return s[:n]


# ── sw ──
sw_factors = [c for c in pd.read_csv(SW_CSV, nrows=0).columns if c not in ("date", "ts_code")]
for f in sw_factors:
    rows.append({
        "factor_id": f"sw.{f}", "factor_name": f, "source": "sw",
        "category_cn": SW_CATEGORY.get(f, "未分类"),
        "frequency": "monthly", "format": "wide",
        "universe": "全A含退市", "history_start": "2016-01",
        "pit_status": "月末快照,滞后~T+7;PIT待隔月diff验证;退市股无幸存者偏差",
        "query_method": FACADE + " 或 GET /data/sw_factor_value(宽表)",
        "description": "申万宏源金工月度因子(z-score标准化)",
        "warning": "回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明",
    })

# ── ts ──
ts_cat = json.load(open(TS_CATALOG))
for f in ts_cat["factors"]:
    rows.append({
        "factor_id": f"ts.{f['name']}", "factor_name": f["name"], "source": "ts",
        "category_cn": f.get("category_cn", f.get("category", "")),
        "frequency": "ondemand", "format": "long",
        "universe": "全A~5500(财务比率类剔除金融~113只)",
        "history_start": "2010-01(价格类)/2010-03(财务类)",
        "pit_status": "试用库(tushare doc_id=486);口径混合:部分[0,1]排名标准化/部分原始值,逐因子核对",
        "query_method": FACADE,
        "description": trunc(f.get("desc", "")),
        "warning": "限频~1次/秒;code后缀勿写错(.SH/.SZ)",
    })

# ── cons ──
cons_factors = sorted(set(pd.read_csv(CONS_CSV, usecols=["factor_name"])["factor_name"]))
for f in cons_factors:
    rows.append({
        "factor_id": f"cons.{f}", "factor_name": f, "source": "cons",
        "category_cn": CONS_CATEGORY.get(f, "预期"),
        "frequency": "weekly", "format": "long",
        "universe": "一致预期覆盖~2725只", "history_start": "2017(面板)",
        "pit_status": "本地快照衍生;增速类要求基期EPS>0;口径详见 recipes/consensus_factors.yaml",
        "query_method": FACADE + " 或 GET /data/consensus_factors(长表)",
        "description": "分析师一致预期因子(预期水平5+预期修正2)",
        "warning": "pe_fwd_1y价格与EPS快照日可能差1-3天",
    })

# ── cjpy ──
try:
    import cjpy
    lf = cjpy.list_factors()
    if not isinstance(lf, pd.DataFrame):
        lf = pd.DataFrame(lf)
    name_col = next((c for c in lf.columns if str(c) in ("因子名称", "名称", "name")), None)
    if name_col is None:
        lf = lf.reset_index()
        name_col = lf.columns[0]
    formula_col = next((c for c in lf.columns if "公式" in str(c)), None)
    desc_col = next((c for c in lf.columns if "说明" in str(c)), None)
    for _, r in lf.iterrows():
        fname = str(r[name_col])
        desc = trunc(r.get(desc_col, "")) if desc_col else ""
        if formula_col:
            desc = (desc + " | 公式:" + trunc(r.get(formula_col), 60)).strip(" |")
        rows.append({
            "factor_id": f"cjpy.{fname}", "factor_name": fname, "source": "cjpy",
            "category_cn": "天软服务端因子",
            "frequency": "server", "format": "query",
            "universe": "天软全A", "history_start": "",
            "pit_status": "服务端实时计算;口径见公式",
            "query_method": FACADE + "(需同时给 trade_date 和 ts_code)",
            "description": desc,
            "warning": "查询走天软TS-OPI,大批量用cjpy Python API分批",
        })
except Exception as e:  # cjpy 不可用时目录仍要能构建
    print(f"[warn] cjpy catalog skipped: {e}")

df = pd.DataFrame(rows, columns=[
    "factor_id", "factor_name", "source", "category_cn", "frequency", "format",
    "universe", "history_start", "pit_status", "query_method", "description", "warning",
])
dup = df["factor_id"].duplicated().sum()
if dup:
    print(f"[warn] {dup} duplicated factor_id")

OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
tmp = OUT_CSV.with_suffix(".csv.tmp")
df.to_csv(tmp, index=False)
os.replace(tmp, OUT_CSV)
print(f"factor_catalog: {len(df)} factors "
      f"(sw={sum(1 for r in rows if r['source']=='sw')}, "
      f"ts={sum(1 for r in rows if r['source']=='ts')}, "
      f"cons={sum(1 for r in rows if r['source']=='cons')}, "
      f"cjpy={sum(1 for r in rows if r['source']=='cjpy')}) -> {OUT_CSV}")

#!/usr/bin/env python3
"""P1 验收: CSV 源 vs ts202_parquet 全量一致性。

PASS 条件（fail-closed）:
  1. 总行数: manifest.source_rows == CSV 实际数据行数 == sum(因子 rows)
  2. 每因子行数: CSV 分块 groupby 计数 == manifest 逐一相等
  3. 抽样 5 因子(固定+按行数分层) 值逐格相等（outer-merge both + 零错值 + 零孤儿）
"""
import json
import sys

import pandas as pd
import pyarrow.parquet as pq

SRC = "/data/fmdata/store/factors/ts_factor_value_v2.csv"
ROOT = "/data/fmdata/store/factors/ts202_parquet"
MAN = f"{ROOT}/manifest.json"

man = json.load(open(MAN))
fac = man["factors"]
print(f"manifest: factors={man['n_factors']} rows={man['source_rows']:,} sha={man['source_sha256'][:16]}...")

# 1+2: CSV 单遍分块计数
counts = {}
total = 0
for ch in pd.read_csv(SRC, usecols=["factor_name"], chunksize=2_000_000):
    total += len(ch)
    for k, v in ch["factor_name"].value_counts().items():
        counts[k] = counts.get(k, 0) + int(v)
print(f"csv rows={total:,} factors={len(counts)}")
ok = True
if total != man["source_rows"]:
    print(f"FAIL 总行数: csv={total:,} != manifest={man['source_rows']:,}"); ok = False
man_counts = {f: r["rows"] for f, r in fac.items()}
diff = {f: (counts.get(f), man_counts.get(f)) for f in set(counts) | set(man_counts)
        if counts.get(f) != man_counts.get(f)}
if diff:
    print(f"FAIL 因子行数不一致 {len(diff)} 个: {list(diff.items())[:5]}"); ok = False
else:
    print(f"因子行数: {len(man_counts)} 个全等 PASS")

# 3: 抽样值校验（首尾+行数分位）
import numpy as np
names_sorted = sorted(man_counts, key=lambda f: man_counts[f])
sample = [names_sorted[0], names_sorted[len(names_sorted)//4], names_sorted[len(names_sorted)//2],
          names_sorted[3*len(names_sorted)//4], names_sorted[-1]]
print(f"抽样因子: {sample}")
for f in sample:
    rec = fac[f]
    parts = [pq.read_table(f"{ROOT}/{f}/{fn}").to_pandas() for fn in rec["files"]]
    par = pd.concat(parts, ignore_index=True)
    csv = []
    for ch in pd.read_csv(SRC, usecols=["factor_name","trade_date","ts_code","factor_value"],
                          dtype={"trade_date":"int32"}, chunksize=2_000_000):
        csv.append(ch[ch["factor_name"] == f].drop(columns=["factor_name"]))
    csv = pd.concat(csv, ignore_index=True)
    if len(csv) != len(par) or len(csv) != rec["rows"]:
        print(f"FAIL {f}: csv={len(csv)} parquet={len(par)} manifest={rec['rows']}"); ok = False; continue
    m = csv.merge(par, on=["trade_date","ts_code"], suffixes=("_c","_p"), how="outer", indicator=True)
    mm = m["_merge"].value_counts().to_dict()
    both = m[m["_merge"]=="both"]
    bad = int((~((both.factor_value_c.isna() & both.factor_value_p.isna()) |
                 (both.factor_value_c == both.factor_value_p))).sum())
    if mm.get("both",0) != len(csv) or bad:
        print(f"FAIL {f}: merge={mm} bad_cells={bad}"); ok = False
    else:
        print(f"{f}: rows={len(csv):,} 值全等 PASS")

print("VERIFY", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

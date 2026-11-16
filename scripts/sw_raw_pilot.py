#!/usr/bin/env python3
"""SW 原始值方案试点(小样): 全量拉取放行前的五项检查。
①列名映射与返回键一致 ②数值合理性 ③与早上服务端z值的秩一致性(spearman≥0.95)
④单调用延迟 ⑤小样本覆盖率。全过才放行全量。
"""
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

CODES = ["000001.SZ", "000002.SZ", "600519.SH", "300750.SZ", "601318.SH",
         "000651.SZ", "600036.SH", "002594.SZ", "688981.SH", "601899.SH",
         "000333.SZ", "600030.SH", "002415.SZ", "300059.SZ", "600276.SH",
         "000858.SZ", "601166.SH", "002304.SZ", "600900.SH", "601088.SH"]
TABLES = ["CN_ASHARE_STYLE/CN_ASHARE_STYLE_VALUE",
          "CN_ASHARE_STYLE/CN_ASHARE_STYLE_MOMENTUM",
          "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Value",
          "CN_ASHARE_ML/CN_ASHARE_ML_LGBM"]


def spearman(a, b):
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    ra, rb = ra - ra.mean(), rb - rb.mean()
    d = float((ra * ra).sum() * (rb * rb).sum())
    return float((ra * rb).sum() / np.sqrt(d)) if d > 0 else np.nan


cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
verdict = {}

# ④延迟 + ①列名 + ⑤覆盖 (用 2026-09-30)
for t in TABLES:
    t0 = time.time()
    try:
        r = cli.call("factorlib_query_factor", {"name": t, "start": "20260930", "end": "20260930", "codes": CODES}, 60)
        lat = time.time() - t0
        rows = r.get("data", [])
        keys = sorted({k for x in rows for k in x if k not in ("TRADE_DATE", "CODE")})
        print(f"[{t.split('/')[-1]}] lat={lat:.2f}s rows={len(rows)}/{len(CODES)} keys={keys}")
        verdict[t] = (lat, len(rows), keys)
    except Exception as e:
        print(f"[{t.split('/')[-1]}] FAIL: {str(e)[:120]}")
        verdict[t] = (None, 0, [])

# ②数值合理性 + ③秩一致性 (2016-01-31 vs 早上服务端z)
r16 = {}
for t in ["CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Value", "CN_ASHARE_STYLE/CN_ASHARE_STYLE_MOMENTUM"]:
    try:
        rr = cli.call("factorlib_query_factor", {"name": t, "start": "20160131", "end": "20160131", "codes": CODES}, 60)
        r16[t.split("/")[-1]] = {x["CODE"]: x for x in rr.get("data", [])}
    except Exception as e:
        print(f"FAIL {t}: {str(e)[:100]}")

disk = pd.read_csv("/home/ubuntu/fmdata/store/factors/sw_factor_value_v2.csv")
d16 = disk[disk["date"] == "2016-01-31"].set_index("ts_code")
common = [c for c in CODES if c in d16.index and c in r16.get("CN_ASHARE_STYLE_COMPOSITE_Value", {})]
if len(common) >= 8:
    raw_v = [r16["CN_ASHARE_STYLE_COMPOSITE_Value"][c]["CN_ASHARE_STYLE_COMPOSITE_Value"] for c in common]
    z_v = [d16.loc[c, "估值"] for c in common]
    print(f"\n秩一致性 raw估值 vs server-z估值 (n={len(common)}): spearman={spearman(raw_v, z_v):.4f}")
    print(f"raw估值 样本: {[f'{v:.3f}' for v in raw_v[:6]]}")
    print(f"server-z估值 样本: {[f'{v:.3f}' for v in z_v[:6]]}")
if common and "CN_ASHARE_STYLE_MOMENTUM" in r16:
    cm2 = [c for c in common if c in r16["CN_ASHARE_STYLE_MOMENTUM"]]
    if len(cm2) >= 8:
        raw_m = [r16["CN_ASHARE_STYLE_MOMENTUM"][c]["CN_ASHARE_STYLE_MOMENTUM"] for c in cm2]
        z_m = [d16.loc[c, "动量"] for c in cm2]
        print(f"秩一致性 raw动量 vs server-z动量 (n={len(cm2)}): spearman={spearman(raw_m, z_m):.4f}")
        print(f"raw动量 样本: {[f'{v:.3f}' for v in raw_m[:6]]}")

# ②EP 合理性
vt = verdict.get(TABLES[0])
if vt and vt[1] > 0:
    r = cli.call("factorlib_query_factor", {"name": TABLES[0], "start": "20260930", "end": "20260930", "codes": CODES}, 60)
    eps = [x.get("CN_ASHARE_STYLE_VALUE__EP") for x in r.get("data", [])]
    print(f"\nEP 样本(n={len(eps)}): min={min(v for v in eps if v is not None):.4f} max={max(v for v in eps if v is not None):.4f} "
          f"正值占比={np.mean([v > 0 for v in eps if v is not None]):.0%}")

print("\nPILOT DONE")

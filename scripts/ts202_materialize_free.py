#!/usr/bin/env python3
"""ts202 因子库物化: factor_value 按因子×月末全市场截面 → ts202_parquet/<factor>/delta 文件。

2026-11-05 P1 迁移: CSV 长表(6.2G)已转 per-factor Parquet(943M, 源归档 .csv.zst)。
增量语义: Parquet 不可追加 → 每因子目录新增 delta-NNN.parquet + manifest 记账;
读取层 fmdata.factor_store.FactorStore 透明合并。
免费试用 ~1000 次/天: 每日 cron 触发, 断点续跑(已有 factor×trade_date 跳过)。
月末网格与 SW v2 对齐(2020-01=20200123); 含定向窗 2016-2020。
"""
import json
import os
import time
from datetime import datetime

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

load_dotenv("/data/fmdata/.env")
import tushare as ts

PARQUET_ROOT = "/data/fmdata/store/factors/ts202_parquet"
MANIFEST = os.path.join(PARQUET_ROOT, "manifest.json")
OUT = "/data/fmdata/store/factors/ts_factor_value_v2.csv"  # 已退役(2026-11-05), 保留常量防误引用
DAILY_CALL_CAP = 10**9  # 2026-11-05 付费开通, 免日配额熔断; 限频熔断保留
SLEEP = 0.16  # 6.7次/s 实测安全

_SCHEMA = pa.schema([
    ("trade_date", pa.int32()),
    ("ts_code", pa.string()),
    ("factor_value", pa.float64()),
])

MONTH_ENDS = ['20160129', '20160229', '20160331', '20160429', '20160531', '20160630', '20160729', '20160831', '20160930', '20161031', '20161130', '20161230', '20170126', '20170228', '20170331', '20170428', '20170531', '20170630', '20170731', '20170831', '20170929', '20171031', '20171130', '20171229', '20180131', '20180228', '20180330', '20180427', '20180531', '20180629', '20180731', '20180831', '20180928', '20181031', '20181130', '20181228', '20190131', '20190228', '20190329', '20190430', '20190531', '20190628', '20190731', '20190830', '20190930', '20191031', '20191129', '20191231', '20200123', '20200228', '20200331', '20200430', '20200529', '20200630', '20200731', '20200831', '20200930', '20201030', '20201130', '20201231', '20210129', '20210226', '20210331', '20210430', '20210531', '20210630', '20210730', '20210831', '20210930', '20211029', '20211130', '20211231', '20220128', '20220228', '20220331', '20220429', '20220531', '20220630', '20220729', '20220831', '20220930', '20221031', '20221130', '20221230', '20230131', '20230228', '20230331', '20230428', '20230531', '20230630', '20230731', '20230831', '20230928', '20231031', '20231130', '20231229', '20240131', '20240229', '20240329', '20240430', '20240531', '20240628', '20240731', '20240830', '20240930', '20241031', '20241129', '20241231', '20250127', '20250228', '20250331', '20250430', '20250530', '20250630', '20250731', '20250829', '20250930', '20251031', '20251128', '20251231', '20260130', '20260227', '20260331', '20260430', '20260529', '20260630', '20260731', '20260831', '20260930', '20261030']  # 交易日历真实月末(trade_cal)


def log(m):
    print(f"[{datetime.now().strftime('%F %T')}] {m}", flush=True)


def factor_list():
    with open("/data/fmdata/store/ts_factor_names_all.txt") as f:
        names = [x.strip() for x in f if x.strip()]
    return names


def done_keys():
    """从 parquet manifest+文件重建 (factor, trade_date_str) 已有集合。"""
    have = set()
    if not os.path.exists(MANIFEST):
        return have
    man = json.load(open(MANIFEST))
    for f, rec in man["factors"].items():
        for fn in rec["files"]:
            t = pq.read_table(os.path.join(PARQUET_ROOT, f, fn), columns=["trade_date"])
            have |= {(f, str(d)) for d in t.column("trade_date").to_pylist()}
    return have


def main():
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    factors = factor_list()
    have = done_keys()
    todo = [(f, d) for f in factors for d in MONTH_ENDS if (f, d) not in have]
    log(f"factors={len(factors)} 月={len(MONTH_ENDS)} 已有={len(have)} 待拉={len(todo)} (预计 {len(todo)/1000:.0f} 天免费配额)")
    calls = 0
    rows_buf = []
    for f, d in todo:
        if calls >= DAILY_CALL_CAP:
            log(f"当日 {calls} 次熔断, 剩 {len(todo)-calls} 明日续")
            break
        try:
            df = pro.factor_value(trade_date=d, factor_name=f)
        except Exception as e:
            msg = str(e)
            if "频率" in msg or "配额" in msg or "每分钟" in msg:
                log(f"配额/限频熔断于 {calls} 次: {msg[:80]}")
                break
            log(f"ERR {f}@{d}: {msg[:80]}")
            time.sleep(3)
            calls += 1
            continue
        calls += 1
        if df is not None and not df.empty:
            rows_buf.append(pd.DataFrame({"factor_name": f, "trade_date": d,
                                          "ts_code": df["ts_code"], "factor_value": df["factor_value"]}))
        time.sleep(SLEEP)
        if len(rows_buf) >= 40:
            _flush(rows_buf); rows_buf = []
    if rows_buf:
        _flush(rows_buf)
    log(f"TS202 批次完成: 本日 {calls} 次, 累计待拉剩 {max(0, len(todo)-calls)}")


def _flush(buf):
    """按因子分组写 delta parquet + 原子更新 manifest。"""
    if not buf:
        return
    df = pd.concat(buf, ignore_index=True)
    man = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {"factors": {}}
    facs = man.setdefault("factors", {})
    for f, g in df.groupby("factor_name"):
        g = g.drop(columns=["factor_name"]).copy()
        g["trade_date"] = g["trade_date"].astype("int32")
        g = g.sort_values(["trade_date", "ts_code"], kind="mergesort")
        d = os.path.join(PARQUET_ROOT, f)
        os.makedirs(d, exist_ok=True)
        seq = len([x for x in os.listdir(d) if x.endswith(".parquet")])
        fname = "base.parquet" if seq == 0 else f"delta-{seq:03d}.parquet"
        tbl = pa.Table.from_pandas(g[["trade_date", "ts_code", "factor_value"]],
                                   schema=_SCHEMA, preserve_index=False)
        pq.write_table(tbl, os.path.join(d, fname), compression="zstd",
                       compression_level=3, use_dictionary=["ts_code"],
                       write_statistics=True, row_group_size=65_536)
        rec = facs.setdefault(f, {"files": [], "rows": 0, "min_date": None, "max_date": None})
        rec["files"].append(fname)
        rec["rows"] += int(len(g))
        rec["min_date"] = int(g["trade_date"].min()) if rec["min_date"] is None else min(rec["min_date"], int(g["trade_date"].min()))
        rec["max_date"] = int(g["trade_date"].max()) if rec["max_date"] is None else max(rec["max_date"], int(g["trade_date"].max()))
        log(f"flush {f}: {fname} +{len(g)}")
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(man, fh, ensure_ascii=False)
    os.replace(tmp, MANIFEST)


if __name__ == "__main__":
    main()

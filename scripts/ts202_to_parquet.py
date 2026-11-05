#!/usr/bin/env python3
"""P1: ts_factor_value_v2.csv (6.2G, 1.45亿行, 长表) → per-factor Parquet。

设计（2026-11-05 存储优化方案, ChatGPT 建议的本地化简化版）：
- 源文件按构造因子主序（ts202_materialize_free.py 的 todo=factor-major）
  → 单遍流式读，因子切换时整固排序写出，内存只驻留一个因子
- 尾部断点续跑散块（因子复现）→ 该因子目录下 delta-<seq>.parquet
- 每因子目录: store/factors/ts202_parquet/<factor>/{base,delta-*}.parquet
  行组 65,536 内部按 (trade_date, ts_code) 排序 → date 统计可裁剪
- 编码: zstd L3, ts_code 字典, float64 原样, trade_date int32(YYYYMMDD 原样)
- manifest.json: 每因子 rows/files/min/max_date + 源文件 sha256/行数
用法: python3 ts202_to_parquet.py [--limit-chunks N]  (测试用)
"""
import hashlib
import json
import os
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

SRC = "/data/fmdata/store/factors/ts_factor_value_v2.csv"
OUTDIR = "/data/fmdata/store/factors/ts202_parquet"
MANIFEST = os.path.join(OUTDIR, "manifest.json")
CHUNK = 2_000_000
COLS = ["factor_name", "trade_date", "ts_code", "factor_value"]
SCHEMA = pa.schema([
    ("trade_date", pa.int32()),
    ("ts_code", pa.string()),
    ("factor_value", pa.float64()),
])

written = {}   # factor -> {"files": [...], "rows": n}
open_factor = None
buf = []       # list of DataFrame pieces for current factor
nrows_total = 0
t0 = time.time()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def flush_factor(factor):
    """整固当前因子 buffer: 排序 + 写 parquet(delta 序号) 。"""
    global buf
    if factor is None or not buf:
        return
    df = pd.concat(buf, ignore_index=True)
    buf = []
    df = df.sort_values(["trade_date", "ts_code"], kind="mergesort")
    d = os.path.join(OUTDIR, factor)
    os.makedirs(d, exist_ok=True)
    seq = len([f for f in os.listdir(d) if f.endswith(".parquet")])
    fname = "base.parquet" if seq == 0 else f"delta-{seq:03d}.parquet"
    tbl = pa.Table.from_pandas(df[["trade_date", "ts_code", "factor_value"]],
                               schema=SCHEMA, preserve_index=False)
    pq.write_table(tbl, os.path.join(d, fname), compression="zstd",
                   compression_level=3, use_dictionary=["ts_code"],
                   write_statistics=True, row_group_size=65_536)
    rec = written.setdefault(factor, {"files": [], "rows": 0,
                                      "min_date": None, "max_date": None})
    rec["files"].append(fname)
    rec["rows"] += len(df)
    rec["min_date"] = int(df["trade_date"].min()) if rec["min_date"] is None else min(rec["min_date"], int(df["trade_date"].min()))
    rec["max_date"] = int(df["trade_date"].max()) if rec["max_date"] is None else max(rec["max_date"], int(df["trade_date"].max()))
    log(f"{factor}: {fname} rows={len(df)} cum={rec['rows']}")

def route_chunk(df):
    """把 chunk 按因子边界切片路由到 buffer。"""
    global open_factor, nrows_total
    nrows_total += len(df)
    ch = df["factor_name"].values
    if len(ch) == 0:
        return
    # 因子边界: 值变化处
    change = [0] + [i for i in range(1, len(ch)) if ch[i] != ch[i-1]] + [len(ch)]
    for s, e in zip(change[:-1], change[1:]):
        f = ch[s]
        if open_factor is not None and f != open_factor:
            flush_factor(open_factor)
        open_factor = f
        buf.append(df.iloc[s:e][COLS])

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    limit = None
    if "--limit-chunks" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit-chunks") + 1])
    n = 0
    for chunk in pd.read_csv(SRC, chunksize=CHUNK, dtype={"trade_date": "int32"},
                             usecols=COLS):
        route_chunk(chunk)
        n += 1
        if n % 10 == 0:
            el = time.time() - t0
            log(f"...chunks={n} rows={nrows_total:,} elapsed={el:.0f}s "
                f"({nrows_total/max(el,1):,.0f} rows/s)")
        if limit and n >= limit:
            log(f"limit-chunks={limit} reached, stopping (测试模式)")
            break
    flush_factor(open_factor)
    # 源文件 sha256 (转换后再算一次太慢 6.2G~1min 可接受; 归档步骤会复核)
    log("hashing source (for manifest)...")
    h = hashlib.sha256()
    with open(SRC, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 24), b""):
            h.update(b)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": SRC,
        "source_sha256": h.hexdigest(),
        "source_rows": nrows_total,
        "n_factors": len(written),
        "factors": written,
    }
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(manifest, fh, ensure_ascii=False)
    os.replace(tmp, MANIFEST)
    log(f"DONE factors={len(written)} rows={nrows_total:,} "
        f"elapsed={(time.time()-t0)/60:.1f}min -> {MANIFEST}")

if __name__ == "__main__":
    main()

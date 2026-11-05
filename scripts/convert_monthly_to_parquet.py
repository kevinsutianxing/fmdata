#!/usr/bin/env python3
"""P0: SW 月度三件套 CSV → Parquet(zstd L3) 副本 + 逐块校验。

设计（对齐 2026-11-05 存储优化方案）：
- 单文件、按 (date, ts_code) 排序、row_group 65,536 → 行组 date 统计可裁剪
- ts_code 字典编码；数值 float64 原样（禁降 float32）
- CSV 保持不动（正本暂不切换，parquet 作为快速读取层）
"""
import os
import sys
import time
import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd

STORE = "/home/ubuntu/fmdata/store/factors"
TABLES = [
    "sw_raw_monthend_v2",
    "sw_chip_monthend_v2",
    "sw_neutral_monthend",
]

def convert(name):
    src = f"{STORE}/{name}.csv"
    dst = f"{STORE}/{name}.parquet"
    t0 = time.time()
    df = pd.read_csv(src)
    df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(tbl, dst, compression="zstd", compression_level=3,
                   use_dictionary=["ts_code", "date"],
                   write_statistics=True, row_group_size=65_536)
    w = time.time() - t0

    # 校验: 行数/列名/逐块值全等(排序后对齐)
    ok = True
    pf = pq.ParquetFile(dst)
    assert pf.metadata.num_rows == len(df), f"{name}: 行数不等"
    assert pf.schema.names == list(df.columns), f"{name}: 列名不等"
    par = pf.read().to_pandas()
    n_bad = 0
    for col in df.columns:
        if col == "ts_code":
            eq = (df[col].astype(str) == par[col].astype(str))
        else:
            a, b = df[col], par[col]
            eq = (a.isna() & b.isna()) | (a == b)
        n_bad += int((~eq).sum())
    ok = n_bad == 0
    print(f"{name}: rows={len(df)} cols={len(df.columns)} "
          f"csv={os.path.getsize(src)/1e6:.0f}M parquet={os.path.getsize(dst)/1e6:.0f}M "
          f"verify={'PASS' if ok else f'FAIL({n_bad} cells)'} [{w:.1f}s]")
    return ok

if __name__ == "__main__":
    allok = all(convert(n) for n in TABLES)
    sys.exit(0 if allok else 1)

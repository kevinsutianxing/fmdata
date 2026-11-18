#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fmdata store CSV -> Parquet 双格式转换器(2026-11-18)

目标:给存量 CSV 数据集生成 zstd 压缩 parquet 副本,读取快 3-10 倍、体积约 1/4。
零破坏设计:
  - 原 CSV 一律保留(recipe 刷新链、代码内 read_csv、研究脚本全部不受影响);
  - server /data/{name} 端点仅当 parquet 的 mtime >= csv 的 mtime 时才读 parquet,
    否则自动回退 CSV —— auto-refresh 刷新 CSV 后旧 parquet 自动失效,不会出陈旧数据;
  - 幂等:parquet 已比 CSV 新则跳过;--force 强制重转;
  - 大文件分块读取(chunksize),单文件内存占用有界(1.1G CSV 也不会 OOM)。

用法:
  python3 convert_store_parquet.py --dry-run        # 只看范围
  python3 convert_store_parquet.py                  # 转换(默认阈值 10MB)
  python3 convert_store_parquet.py --min-mb 1       # 调低阈值
"""
import argparse
import json
import os
import sys
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

STORE = "/data/fmdata/store"
MANIFEST = "/data/fmdata/store/.parquet_manifest.json"


def iter_csvs(min_bytes):
    for root, _, files in os.walk(STORE):
        rel_root = os.path.relpath(root, STORE)
        # _snapshots/ = research snapshot 冻结证据(原始字节+hash 语义),绝不触碰;
        # RETIRED = 已退役数据源,不浪费算力。
        if rel_root.startswith("_snapshots") or "RETIRED" in rel_root:
            continue
        for fn in sorted(files):
            if not fn.endswith(".csv") or "RETIRED" in fn:
                continue
            p = os.path.join(root, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz < min_bytes:
                continue
            yield p, sz


def convert(csv_path, force=False):
    """CSV -> .parquet(zstd),pyarrow 原生流式:内存恒定 ≈ block_size 级,不随文件增长。

    2026-11-18 教训:旧 pandas chunksize 版在宽表(100+列)上每块都要物化+类型推断,
    2-3 个并发大文件就把 3.6G 主机顶到 OOM(convert 进程被 SIGKILL、fmdata 响应 6s+)。
    pyarrow.csv.open_csv 按 RecordBatch 迭代直写 ParquetWriter,无 pandas 中间层。
    """
    pq_path = csv_path[:-4] + ".parquet"
    csv_m = os.path.getmtime(csv_path)
    if not force and os.path.exists(pq_path) and os.path.getmtime(pq_path) >= csv_m:
        return "SKIP_FRESH", None, None, None, 0.0

    from pyarrow import csv as pacsv
    writer = None
    rows = 0
    t0 = time.time()
    try:
        read_opts = pacsv.ReadOptions(block_size=32 * 1024 * 1024)  # 32MB 块 → 峰值内存有界
        with pacsv.open_csv(csv_path, read_options=read_opts) as reader:
            for batch in reader:
                if writer is None:
                    writer = pq.ParquetWriter(pq_path, batch.schema, compression="zstd")
                writer.write_batch(batch)
                rows += batch.num_rows
        if writer is None:  # 空 CSV
            pq.write_table(pa.table({}), pq_path, compression="zstd")
        writer.close()
        writer = None
        return "OK", rows, os.path.getsize(csv_path), os.path.getsize(pq_path), time.time() - t0
    except Exception as exc:  # 转换失败不破坏:删掉半成品,原 CSV 无损
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        writer = None
        if os.path.exists(pq_path):
            try:
                os.remove(pq_path)
            except OSError:
                pass
        return "FAIL: %s" % str(exc)[:120], None, None, None, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-mb", type=float, default=10.0, help="只转大于该体积的 CSV(默认 10MB)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个(调试)")
    args = ap.parse_args()

    targets = list(iter_csvs(int(args.min_mb * 1024 * 1024)))
    targets.sort(key=lambda x: -x[1])
    total = sum(sz for _, sz in targets)
    print(f"候选 CSV: {len(targets)} 个,合计 {total/1073741824:.2f} GB (阈值 {args.min_mb}MB)", flush=True)
    if args.dry_run:
        for p, sz in targets[:30]:
            print(f"  {sz/1048576:9.1f}MB  {os.path.relpath(p, STORE)}")
        return

    manifest = {}
    if os.path.exists(MANIFEST):
        try:
            manifest = json.load(open(MANIFEST))
        except Exception:
            manifest = {}
    done = 0
    gained = 0
    failed = []
    for p, sz in targets:
        if args.limit and done >= args.limit:
            break
        res = convert(p, force=args.force)
        if len(res) == 5:
            status, rows, csz, psz, secs = res
        else:
            status, rows, csz, psz = res
            secs = 0
        rel = os.path.relpath(p, STORE)
        manifest[rel] = {"status": status.split(":")[0], "rows": rows,
                         "csv_bytes": csz, "parquet_bytes": psz,
                         "converted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if status.startswith("OK"):
            done += 1
            gained += (csz or 0) - (psz or 0)
            print(f"OK   {rel}  rows={rows}  {csz/1048576:.1f}MB->{psz/1048576:.1f}MB  {secs:.1f}s", flush=True)
        elif status.startswith("FAIL"):
            failed.append((rel, status))
            print(f"FAIL {rel}  {status}", flush=True)
        else:
            print(f"SKIP {rel}", flush=True)

    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"\n转换 {done} 个,预估可省 {gained/1073741824:.2f} GB(双份期实际占用另计);失败 {len(failed)}")
    if failed:
        print("失败清单:")
        for rel, st in failed:
            print("  ", rel, st)
        sys.exit(1)


if __name__ == "__main__":
    main()

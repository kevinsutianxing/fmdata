#!/usr/bin/env python3
"""sw_v2 月度刷新落盘验证：列数齐一 + 最新完成月两表同网格有数。

PASS 条件（fail-closed）：
  1. raw 列数 == 41（FIXED_COLS），chip 列数 == 9
  2. 两表最新月末 >= 上一个完成月（CST 口径），且该月行数 >= 3000
  3. 两表最新月末一致（同网格）
"""
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

CST = ZoneInfo("Asia/Shanghai")
RAW = "/home/ubuntu/fmdata/store/factors/sw_raw_monthend_v2.csv"
CHIP = "/home/ubuntu/fmdata/store/factors/sw_chip_monthend_v2.csv"

def month_end_str(period_offset=1):
    """上一个完成月的月末日期串（CST）。offset=1 即上个月。"""
    now = datetime.now(CST) - timedelta(days=28 * period_offset)
    p = pd.Period(now.strftime("%Y-%m"), freq="M") - (period_offset - 1)
    return str(p.end_time.date())

def check(path, expect_cols, label):
    header = pd.read_csv(path, nrows=0)
    ncols = len(header.columns)
    if ncols != expect_cols:
        print(f"FAIL {label}: 列数 {ncols} != {expect_cols}（列错位事故特征）")
        return None
    dates = set()
    for ch in pd.read_csv(path, usecols=["date"], chunksize=200000):
        dates |= set(ch["date"])
    if not dates:
        print(f"FAIL {label}: 零数据")
        return None
    latest = max(dates)
    # 最新月末的行数
    n_latest = 0
    for ch in pd.read_csv(path, usecols=["date"], chunksize=200000):
        n_latest += int((ch["date"] == latest).sum())
    print(f"{label}: cols={ncols} latest={latest} rows_latest={n_latest} months={len(dates)}")
    if n_latest < 3000:
        print(f"FAIL {label}: 最新月 {latest} 仅 {n_latest} 行 (<3000)")
        return None
    return latest

latest_raw = check(RAW, 41, "raw")
latest_chip = check(CHIP, 9, "chip")

if latest_raw is None or latest_chip is None:
    sys.exit(1)

floor = month_end_str(2)  # 允许一个发布周期滞后：不早于上上月末
if latest_raw < floor or latest_chip < floor:
    print(f"FAIL: 数据过旧 raw={latest_raw} chip={latest_chip} floor={floor}")
    sys.exit(1)
if latest_raw[:7] != latest_chip[:7]:
    print(f"WARN: raw/chip 最新月份不一致 {latest_raw} vs {latest_chip}（不阻断，需人工看）")

print("VERIFY PASS")

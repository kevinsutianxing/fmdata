#!/usr/bin/env python3
"""申万金工 16 因子库数据质量报告（回填完成后跑）。"""
import sys
import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR

OUT = STORE_DIR / "factors/sw_factor_value.csv"

df = pd.read_csv(OUT)
df["year"] = df["date"].str[:4]
factors = [c for c in df.columns if c not in ("date", "ts_code", "year")]

print(f"== 总量: {len(df)} 行 × {len(factors)} 因子, 月份 {df['date'].nunique()} 个 ({df['date'].min()} → {df['date'].max()})")
print(f"   股票数: {df['ts_code'].nunique()} (universe 5899)")

print("\n== 每年股票覆盖(行数/月均):")
yc = df.groupby("year").agg(rows=("ts_code", "size"), months=("date", "nunique"))
yc["per_month"] = (yc["rows"] / yc["months"]).round(0).astype(int)
print(yc.to_string())

print("\n== 各因子非NaN覆盖率(按年, %):")
cov = df.groupby("year")[factors].apply(lambda g: g.notna().mean() * 100).round(1)
print(cov.to_string())

print("\n== 退市股覆盖抽查(应非零):")
uni = pd.read_csv(STORE_DIR / "factors/sw_universe.csv")
delisted = uni[uni["status"] == "D"]["ts_code"].head(200)
hit = df[df["ts_code"].isin(delisted)]["ts_code"].nunique()
print(f"   前200退市股中 {hit} 只有历史因子值")

gaps = df.groupby("date")[factors].apply(lambda g: g.notna().mean())
worst = gaps.stack().sort_values()
print("\n== 覆盖最差的5个(月,因子):")
print(worst.head(5).round(3).to_string())

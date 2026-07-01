#!/usr/bin/env python3
"""Derive ETF monthly close pivot from etf_daily."""
import pandas as pd
from pathlib import Path

daily = Path("/home/ubuntu/fmdata/store/market/etf_daily.csv")
output = Path("/home/ubuntu/fmdata/store/market/etf_monthly_close.csv")

df = pd.read_csv(daily)
df["trade_date"] = pd.to_datetime(df["trade_date"])
df["month"] = df["trade_date"].dt.to_period("M")

monthly = df.groupby(["code", "month"])["close"].last().reset_index()
monthly["month"] = monthly["month"].astype(str)
pivot = monthly.pivot(index="month", columns="code", values="close")

output.parent.mkdir(parents=True, exist_ok=True)
pivot.to_csv(output)
print(f"saved {len(pivot)} months x {len(pivot.columns)} ETFs")
